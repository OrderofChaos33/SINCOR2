// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

interface IMorpho {
    struct MarketParams {
        address loanToken;
        address collateralToken;
        address oracle;
        address irm;
        uint256 lltv;
    }

    function createMarket(MarketParams memory marketParams) external;
}

contract SincMorphoSetup is Ownable {
    using SafeERC20 for IERC20;

    // Base mainnet canonical addresses
    address public constant MORPHO_BLUE = 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb;
    address public constant ADAPTIVE_CURVE_IRM = 0x46415998764C29aB2a25CbeA6254146D50D22687;
    address public constant BUNDLER3 = 0x6BFd8137e702540E7A42B74178A4a49Ba43920C4;

    // Common loan tokens on Base
    address public constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address public constant WETH = 0x4200000000000000000000000000000000000006;

    IERC20 public immutable SINC;
    address public immutable ORACLE;

    event MarketCreated(bytes32 indexed marketId, address loanToken, uint256 lltv);

    constructor(address _sinc, address _oracle, address initialOwner) Ownable(initialOwner) {
        require(_sinc != address(0) && _oracle != address(0), "zero address");
        SINC = IERC20(_sinc);
        ORACLE = _oracle;
    }

    /// @notice Create a Morpho market with SINC as collateral
    function deployMarket(address loanToken, address irm, uint256 lltv) public onlyOwner returns (bytes32 marketId) {
        IMorpho.MarketParams memory params = IMorpho.MarketParams({
            loanToken: loanToken,
            collateralToken: address(SINC),
            oracle: ORACLE,
            irm: irm,
            lltv: lltv
        });

        IMorpho(MORPHO_BLUE).createMarket(params);

        // Market id is keccak256 of the packed params (standard Morpho)
        marketId = keccak256(abi.encode(params));
        emit MarketCreated(marketId, loanToken, lltv);
    }

    /// @notice Convenience: create SINC/USDC market with AdaptiveCurve + common LLTV
    function createSincUsdcMarket(uint256 lltv) external onlyOwner returns (bytes32) {
        return deployMarket(USDC, ADAPTIVE_CURVE_IRM, lltv);
    }

    function recoverToken(address token, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(msg.sender, amount);
    }
}
