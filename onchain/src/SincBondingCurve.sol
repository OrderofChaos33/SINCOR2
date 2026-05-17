// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SincGenesisNFT} from "./SincGenesisNFT.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {IPositionManager} from "@uniswap/v4-periphery/src/interfaces/IPositionManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {Currency} from "@uniswap/v4-core/src/types/Currency.sol";
import {IHooks} from "@uniswap/v4-core/src/interfaces/IHooks.sol";
import {Actions} from "@uniswap/v4-periphery/src/libraries/Actions.sol";
import {IERC721} from "@openzeppelin/contracts/token/ERC721/IERC721.sol";

contract SincBondingCurve {
    IERC20 public immutable sinc;
    address public immutable treasury;
    SincGenesisNFT public immutable nft;

    // Constant-product virtual reserves
    // Tuned so initial price (when 0 SINC sold) ≈ 3e-8 ETH per displayed SINC (≈ $0.0001 at ETH=$3000)
    uint256 public constant VIRTUAL_ETH = 3 ether;
    uint256 public constant VIRTUAL_SINC = 100_000_000 * 10**8;

    uint256 public sincSold;
    uint256 public ethAccumulated;
    bool public graduated;
    mapping(address => bool) public hasGenesisNFT;
    uint256 public nextBuyOrderNumber = 1;

    IPoolManager public immutable poolManager;
    IPositionManager public immutable positionManager;
    IHooks public hook;  // set once via setHook before any buy
    address public constant WETH = 0x4200000000000000000000000000000000000006;
    address public constant DEAD = 0x000000000000000000000000000000000000dEaD;
    uint256 public constant GRADUATION_THRESHOLD = 0.5 ether;
    uint24 public constant POOL_FEE = 3000;
    int24 public constant TICK_SPACING = 60;

    event Buy(address indexed buyer, uint256 ethIn, uint256 sincOut, address referrer);
    event Sell(address indexed seller, uint256 sincIn, uint256 ethOut);
    event Graduated(uint256 ethToLP, uint256 ethToTreasury, uint256 sincToLP, uint256 lpTokenId);

    constructor(
        address _sinc,
        address _treasury,
        address _nft,
        address _poolManager,
        address _positionManager
    ) {
        sinc = IERC20(_sinc);
        treasury = _treasury;
        nft = SincGenesisNFT(_nft);
        poolManager = IPoolManager(_poolManager);
        positionManager = IPositionManager(_positionManager);
    }

    function setHook(address _hook) external {
        require(hook == IHooks(address(0)), "Hook already set");
        require(_hook != address(0), "Zero hook");
        hook = IHooks(_hook);
    }

    function currentPriceWei() public view returns (uint256) {
        return (VIRTUAL_ETH + ethAccumulated) * 10**8 / (VIRTUAL_SINC - sincSold);
    }

    function getBuyAmount(uint256 ethIn) public view returns (uint256 sincOut) {
        uint256 k = (VIRTUAL_ETH + ethAccumulated) * (VIRTUAL_SINC - sincSold);
        uint256 newSincRemaining = k / (VIRTUAL_ETH + ethAccumulated + ethIn);
        uint256 rawOut = (VIRTUAL_SINC - sincSold) - newSincRemaining;
        // Subtract 1 atomic unit so the curve always captures the integer-division floor;
        // this guarantees buy→sell round-trips lose value (no lossless arbitrage).
        sincOut = rawOut > 0 ? rawOut - 1 : 0;
    }

    function getBuyCost(uint256 sincOut) public view returns (uint256 ethIn) {
        uint256 k = (VIRTUAL_ETH + ethAccumulated) * (VIRTUAL_SINC - sincSold);
        uint256 newEthVirtual = k / (VIRTUAL_SINC - sincSold - sincOut);
        ethIn = newEthVirtual - (VIRTUAL_ETH + ethAccumulated);
    }

    function getSellRefund(uint256 sincIn) public view returns (uint256 ethOut) {
        uint256 k = (VIRTUAL_ETH + ethAccumulated) * (VIRTUAL_SINC - sincSold);
        uint256 newEthVirtual = k / (VIRTUAL_SINC - sincSold + sincIn);
        ethOut = (VIRTUAL_ETH + ethAccumulated) - newEthVirtual;
    }

    function buy(uint256 ethIn, address referrer) external payable returns (uint256 sincOut) {
        require(!graduated, "Graduated");
        require(msg.value >= ethIn, "Insufficient ETH");
        require(ethIn > 0, "Zero ETH");

        // Referral split: 3% to referrer (or treasury fallback), 97% stays in curve
        uint256 referralCut = (ethIn * 3) / 100;
        uint256 curveCut = ethIn - referralCut;

        // Price SINC against the ETH that actually enters the curve (curveCut),
        // keeping the AMM invariant consistent with ethAccumulated.
        sincOut = getBuyAmount(curveCut);
        require(sincOut > 0, "Zero SINC out");
        require(sincOut <= sinc.balanceOf(address(this)), "Insufficient SINC in curve");

        address referralRecipient = (referrer != address(0) && referrer != msg.sender)
            ? referrer
            : treasury;

        sincSold += sincOut;
        ethAccumulated += curveCut;

        require(sinc.transfer(msg.sender, sincOut), "SINC transfer failed");

        // Auto-mint Genesis NFT on first buy only
        if (!hasGenesisNFT[msg.sender]) {
            hasGenesisNFT[msg.sender] = true;
            nft.mint(msg.sender, nextBuyOrderNumber++);
        }

        // Pay referrer (or treasury fallback)
        (bool refOk,) = referralRecipient.call{value: referralCut}("");
        require(refOk, "Referral payment failed");

        // Refund excess ETH
        if (msg.value > ethIn) {
            (bool ok,) = msg.sender.call{value: msg.value - ethIn}("");
            require(ok, "Refund failed");
        }

        emit Buy(msg.sender, ethIn, sincOut, referralRecipient);
    }

    function sell(uint256 sincIn) external returns (uint256 ethOut) {
        require(!graduated, "Graduated");
        require(sincIn > 0 && sincIn <= sincSold, "Sell exceeds amount sold");

        ethOut = getSellRefund(sincIn);
        require(address(this).balance >= ethOut, "Insufficient reserve");

        sincSold -= sincIn;
        ethAccumulated -= ethOut;

        require(sinc.transferFrom(msg.sender, address(this), sincIn), "SINC transferFrom failed");
        (bool ok,) = msg.sender.call{value: ethOut}("");
        require(ok, "ETH transfer failed");

        emit Sell(msg.sender, sincIn, ethOut);
    }

    function graduate() external {
        require(!graduated, "Already graduated");
        require(ethAccumulated >= GRADUATION_THRESHOLD, "Below threshold");
        require(hook != IHooks(address(0)), "Hook not set");

        graduated = true;

        uint256 ethToLP = (ethAccumulated * 80) / 100;
        uint256 ethToTreasury = ethAccumulated - ethToLP;
        uint256 sincToLP = sinc.balanceOf(address(this));

        (Currency currency0, Currency currency1) = address(sinc) < WETH
            ? (Currency.wrap(address(sinc)), Currency.wrap(WETH))
            : (Currency.wrap(WETH), Currency.wrap(address(sinc)));

        PoolKey memory poolKey = PoolKey({
            currency0: currency0,
            currency1: currency1,
            fee: POOL_FEE,
            tickSpacing: TICK_SPACING,
            hooks: hook
        });

        uint160 sqrtPriceX96 = _computeSqrtPriceX96(ethToLP, sincToLP);

        // IPoolManager.initialize signature (from lib/v4-core/src/interfaces/IPoolManager.sol):
        //   function initialize(PoolKey memory key, uint160 sqrtPriceX96) external returns (int24 tick);
        poolManager.initialize(poolKey, sqrtPriceX96);

        sinc.approve(address(positionManager), sincToLP);

        uint256 lpTokenId = positionManager.nextTokenId();
        bytes memory actions = abi.encodePacked(uint8(Actions.MINT_POSITION), uint8(Actions.SETTLE_PAIR));
        bytes[] memory params = new bytes[](2);
        params[0] = abi.encode(
            poolKey,
            int24(-887220),  // TickMath.MIN_TICK aligned to spacing 60
            int24(887220),
            sincToLP,
            uint128(sincToLP),
            uint128(ethToLP),
            address(this),
            bytes("")
        );
        params[1] = abi.encode(currency0, currency1);

        positionManager.modifyLiquidities{value: ethToLP}(abi.encode(actions, params), block.timestamp + 60);

        IERC721(address(positionManager)).transferFrom(address(this), DEAD, lpTokenId);

        (bool ok,) = treasury.call{value: ethToTreasury}("");
        require(ok, "Treasury transfer failed");

        emit Graduated(ethToLP, ethToTreasury, sincToLP, lpTokenId);
    }

    function _computeSqrtPriceX96(uint256 ethReserve, uint256 sincReserve) internal pure returns (uint160) {
        uint256 priceRatio = (ethReserve * 1e18) / sincReserve;
        uint256 sqrtPrice = _sqrt(priceRatio);
        return uint160((sqrtPrice * (1 << 96)) / 1e9);
    }

    function _sqrt(uint256 x) internal pure returns (uint256) {
        if (x == 0) return 0;
        uint256 z = (x + 1) / 2;
        uint256 y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
        return y;
    }

    function onERC721Received(address, address, uint256, bytes calldata) external pure returns (bytes4) {
        return this.onERC721Received.selector;
    }

    receive() external payable {}
}
