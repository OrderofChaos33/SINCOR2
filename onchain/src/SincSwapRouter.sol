// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IERC721Receiver} from "@openzeppelin/contracts/token/ERC721/IERC721Receiver.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";

import {ISincPriceOracle, ISincSwapRouter} from "./interfaces/ISincLoop.sol";
import {IAerodromeRouter, ISincBondingCurve, IWETH9} from "./interfaces/ISincLoopInfra.sol";

/// @title SincSwapRouter — production USDC<->SINC swap router for lending loops
/// @notice Upgraded successor to `contracts/SINCAMMRouter.sol` (feature/sincor-consolidation),
///         narrowed to the ISincSwapRouter surface consumed by SINCLending.
///
///         Route design (verified onchain 2026-07-20 — the SINC/USDC + SINC/WETH AMM pools
///         are dust; the bonding curve IS the live SINC market):
///           buy : USDC --(Aerodrome USDC/WETH volatile)--> WETH -> ETH --(curve.buy)--> SINC
///           sell: SINC --(curve.sell)--> ETH -> WETH --(Aerodrome)--> USDC
///
///         Slippage model (interface has no minOut params, so bounds are internal):
///           - every AMM leg is bounded against the Aerodrome spot quote (quoteSlippageBps);
///           - the aggregate output is bounded against the production oracle
///             (maxSlippageBps) — this is the invariant SINCLending's health math relies on.
///
///         Notes:
///           - curve.buy() takes a 3% referral cut (routed to treasury fallback), so
///             maxSlippageBps must exceed 300 bps or buys revert by design.
///           - the curve auto-mints a soulbound Genesis NFT to first-time buyers; this
///             contract accepts it via IERC721Receiver (harmless, one-time).
contract SincSwapRouter is ISincSwapRouter, Ownable, ReentrancyGuard, IERC721Receiver {
    using SafeERC20 for IERC20;

    uint256 public constant BPS = 10_000;
    uint256 public constant MAX_SLIPPAGE_CAP = 1_000; // 10% hard ceiling

    IERC20 public immutable SINC;
    IERC20 public immutable USDC;
    IWETH9 public immutable WETH;
    ISincBondingCurve public immutable curve;
    IAerodromeRouter public immutable aeroRouter;
    address public immutable aeroFactory;
    uint256 public immutable sincUnit; // 10 ** SINC.decimals()

    ISincPriceOracle public oracle;
    uint256 public maxSlippageBps = 500; // 5% aggregate bound vs oracle (curve takes 3%)
    uint256 public quoteSlippageBps = 100; // 1% per AMM leg vs Aerodrome spot quote

    event SwapUSDCForSINC(address indexed user, uint256 usdcIn, uint256 sincOut);
    event SwapSINCForUSDC(address indexed user, uint256 sincIn, uint256 usdcOut);
    event OracleUpdated(address oracle);
    event SlippageUpdated(uint256 maxSlippageBps, uint256 quoteSlippageBps);
    event Rescued(address token, address to, uint256 amount);

    error ZeroAddress();
    error ZeroInput();
    error Slippage(uint256 expected, uint256 actual);
    error BadSlippageConfig();

    constructor(
        address _sinc,
        address _usdc,
        address _weth,
        address _curve,
        address _aeroRouter,
        address _aeroFactory,
        ISincPriceOracle _oracle,
        address _owner
    ) Ownable(_owner) {
        if (
            _sinc == address(0) || _usdc == address(0) || _weth == address(0) || _curve == address(0)
                || _aeroRouter == address(0) || _aeroFactory == address(0) || address(_oracle) == address(0)
                || _owner == address(0)
        ) {
            revert ZeroAddress();
        }
        SINC = IERC20(_sinc);
        USDC = IERC20(_usdc);
        WETH = IWETH9(_weth);
        curve = ISincBondingCurve(_curve);
        aeroRouter = IAerodromeRouter(_aeroRouter);
        aeroFactory = _aeroFactory;
        oracle = _oracle;
        sincUnit = 10 ** IERC20Metadata(_sinc).decimals();
    }

    /// @inheritdoc ISincSwapRouter
    function swapUSDCForSINC(uint256 usdcIn) external nonReentrant returns (uint256 sincOut) {
        if (usdcIn == 0) revert ZeroInput();

        // 1. Pull USDC from caller (SINCLending approves before calling).
        USDC.safeTransferFrom(msg.sender, address(this), usdcIn);

        // 2. USDC -> WETH via Aerodrome (deep volatile USDC/WETH pool).
        USDC.forceApprove(address(aeroRouter), usdcIn);
        IAerodromeRouter.Route[] memory routes = _route(address(USDC), address(WETH));
        uint256 quoted = aeroRouter.getAmountsOut(usdcIn, routes)[1];
        uint256 minWeth = FullMath.mulDiv(quoted, BPS - quoteSlippageBps, BPS);
        uint256 wethOut =
            aeroRouter.swapExactTokensForTokens(usdcIn, minWeth, routes, address(this), block.timestamp)[1];

        // 3. Unwrap to ETH.
        WETH.withdraw(wethOut);

        // 4. Buy SINC on the bonding curve; aggregate bound vs oracle price.
        uint256 minSinc = _oracleSincOut(usdcIn);
        uint256 before = SINC.balanceOf(address(this));
        curve.buy{value: wethOut}(wethOut, address(0));
        sincOut = SINC.balanceOf(address(this)) - before;
        if (sincOut < minSinc) revert Slippage(minSinc, sincOut);

        // 5. Deliver SINC to caller.
        SINC.safeTransfer(msg.sender, sincOut);
        emit SwapUSDCForSINC(msg.sender, usdcIn, sincOut);
    }

    /// @inheritdoc ISincSwapRouter
    function swapSINCForUSDC(uint256 sincIn) external nonReentrant returns (uint256 usdcOut) {
        if (sincIn == 0) revert ZeroInput();

        // 1. Pull SINC from caller.
        SINC.safeTransferFrom(msg.sender, address(this), sincIn);

        // 2. Sell into the bonding curve, bounded against its own quote.
        uint256 minEth = FullMath.mulDiv(curve.getSellRefund(sincIn), BPS - quoteSlippageBps, BPS);
        SINC.forceApprove(address(curve), sincIn);
        uint256 ethBefore = address(this).balance;
        curve.sell(sincIn);
        uint256 ethOut = address(this).balance - ethBefore;
        if (ethOut < minEth) revert Slippage(minEth, ethOut);

        // 3. Wrap and swap WETH -> USDC via Aerodrome, bounded by BOTH the pool
        //    quote and the oracle aggregate.
        WETH.deposit{value: ethOut}();
        IERC20(address(WETH)).forceApprove(address(aeroRouter), ethOut);
        IAerodromeRouter.Route[] memory routes = _route(address(WETH), address(USDC));
        uint256 quoted = aeroRouter.getAmountsOut(ethOut, routes)[1];
        uint256 minQuote = FullMath.mulDiv(quoted, BPS - quoteSlippageBps, BPS);
        uint256 minOracle = _oracleUsdcOut(sincIn);
        uint256 minUsdc = minQuote > minOracle ? minQuote : minOracle;
        usdcOut =
            aeroRouter.swapExactTokensForTokens(ethOut, minUsdc, routes, address(this), block.timestamp)[1];

        // 4. Deliver USDC to caller.
        USDC.safeTransfer(msg.sender, usdcOut);
        emit SwapSINCForUSDC(msg.sender, sincIn, usdcOut);
    }

    // --------------------------- internals ---------------------------

    function _route(address from, address to) internal view returns (IAerodromeRouter.Route[] memory routes) {
        routes = new IAerodromeRouter.Route[](1);
        routes[0] = IAerodromeRouter.Route({from: from, to: to, stable: false, factory: aeroFactory});
    }

    /// @dev Expected SINC out at oracle price, less aggregate slippage allowance.
    function _oracleSincOut(uint256 usdcIn) internal view returns (uint256) {
        uint256 expected = FullMath.mulDiv(usdcIn, sincUnit, oracle.sincPriceUSDC());
        return FullMath.mulDiv(expected, BPS - maxSlippageBps, BPS);
    }

    /// @dev Expected USDC out at oracle price, less aggregate slippage allowance.
    function _oracleUsdcOut(uint256 sincIn) internal view returns (uint256) {
        uint256 expected = FullMath.mulDiv(sincIn, oracle.sincPriceUSDC(), sincUnit);
        return FullMath.mulDiv(expected, BPS - maxSlippageBps, BPS);
    }

    // ----------------------------- admin -----------------------------

    function setOracle(ISincPriceOracle _oracle) external onlyOwner {
        if (address(_oracle) == address(0)) revert ZeroAddress();
        oracle = _oracle;
        emit OracleUpdated(address(_oracle));
    }

    function setSlippage(uint256 _maxSlippageBps, uint256 _quoteSlippageBps) external onlyOwner {
        if (_maxSlippageBps > MAX_SLIPPAGE_CAP || _quoteSlippageBps > MAX_SLIPPAGE_CAP) {
            revert BadSlippageConfig();
        }
        maxSlippageBps = _maxSlippageBps;
        quoteSlippageBps = _quoteSlippageBps;
        emit SlippageUpdated(_maxSlippageBps, _quoteSlippageBps);
    }

    /// @notice Rescue ERC-20 tokens (e.g. dust left by refunds). Not a value store by design.
    function rescue(address token, address to, uint256 amount) external onlyOwner {
        if (to == address(0)) revert ZeroAddress();
        IERC20(token).safeTransfer(to, amount);
        emit Rescued(token, to, amount);
    }

    /// @notice Rescue stray ETH.
    function rescueETH(address to, uint256 amount) external onlyOwner {
        if (to == address(0)) revert ZeroAddress();
        (bool ok,) = to.call{value: amount}("");
        require(ok, "ETH transfer failed");
        emit Rescued(address(0), to, amount);
    }

    // --------------------------- receivers ---------------------------

    /// @dev Accepts ETH from WETH.withdraw and curve.sell / curve.buy refunds.
    receive() external payable {}

    /// @dev Accepts the one-time soulbound Genesis NFT minted by the curve on first buy.
    function onERC721Received(address, address, uint256, bytes calldata) external pure returns (bytes4) {
        return IERC721Receiver.onERC721Received.selector;
    }
}
