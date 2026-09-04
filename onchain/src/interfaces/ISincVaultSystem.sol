// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface ISincVaultFactory {
    event VaultDeployed(
        address indexed asset,
        address vault,
        address router,
        address flywheel,
        address psm,
        address riskModule
    );

    function deployVault(
        address asset,
        string calldata name,
        string calldata symbol,
        bytes calldata config
    ) external returns (address vault);
}

interface ISinc4626Vault {
    function asset() external view returns (address);
    function totalAssets() external view returns (uint256);
    function router() external view returns (address);
    function harvest() external returns (uint256 realizedYield, uint256 feeAmount);
    function rebalance() external;
    function setIdleBufferBps(uint16 bps) external;
}

interface ISincStrategyRouter {
    struct StrategyConfig {
        uint16 targetWeightBps;
        uint16 maxDrawdownBps;
        uint16 performanceFeeBps;
        uint16 harvestCooldown;
        uint128 debtCap;
        bool enabled;
    }

    event StrategyAdded(address indexed strategy, StrategyConfig config);
    event StrategyUpdated(address indexed strategy, StrategyConfig config);
    event Rebalanced(uint256 timestamp);

    function addStrategy(address strategy, StrategyConfig calldata cfg) external;
    function updateStrategy(address strategy, StrategyConfig calldata cfg) external;
    function allocate(uint256 amount) external;
    function deallocate(uint256 amount) external returns (uint256 returnedAmount);
    function totalManagedAssets() external view returns (uint256);
    function harvestStrategies() external returns (uint256 grossYield, uint256 loss);
}

interface ISincFeeFlywheel {
    event HarvestSplit(uint256 feeAmount, uint256 buybackAmount, uint256 treasuryAmount);
    event AeroPurchased(uint256 amountIn, uint256 aeroOut);
    event VeAeroLocked(uint256 aeroAmount, uint256 lockEnd);
    event GaugeVoted(address indexed gauge, uint256 weight);

    function processFees(
        address baseAsset,
        uint256 feeAmount,
        address gauge
    ) external returns (uint256 buybackAmount, uint256 treasuryAmount);

    function treasury() external view returns (address);
}

interface ISincPSM {
    event PegBandUpdated(uint8 band, uint256 pegDeviation);
    event Minted(address indexed user, uint256 inAsset, uint256 outSc);
    event Redeemed(address indexed user, uint256 inSc, uint256 outAsset);

    function mint(uint256 assetAmount, address receiver) external returns (uint256 scOut);
    function redeem(uint256 scAmount, address receiver) external returns (uint256 assetOut);
    function currentBand() external view returns (uint8);
    function pegDeviationBps() external view returns (uint256);
}

interface ISincRiskModule {
    struct SwapCheckParams {
        address tokenIn;
        address tokenOut;
        uint256 amountIn;
        uint256 expectedOutOracle;
        uint16 maxSlippageBps;
        uint16 maxOracleDeviationBps;
    }

    event RiskCheckPassed(bytes32 indexed checkId);
    event CircuitBreakerSet(bool paused);

    function validateSwap(SwapCheckParams calldata p) external view returns (uint256 minOut);
    function validatePrice(address token) external view returns (uint256 price, uint256 updatedAt);
    function isPaused() external view returns (bool);
}

interface IAeroExecutionAdapter {
    function swapToAero(
        address tokenIn,
        uint256 amountIn,
        uint256 minAeroOut
    ) external returns (uint256 aeroOut);

    function lockVeAero(uint256 aeroAmount, uint256 lockDuration) external returns (uint256 tokenId);
    function voteGauge(address gauge, uint256 weight) external;
}

interface ISincStrategy {
    function asset() external view returns (address);
    function totalAssets() external view returns (uint256);
    function deposit(uint256 amount) external returns (uint256 deployed);
    function withdraw(uint256 amount, address to) external returns (uint256 returnedAmount);
    function harvest() external returns (uint256 gain, uint256 loss);
}
