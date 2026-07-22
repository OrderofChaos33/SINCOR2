// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

/// @title  FluidInterfaces — Fluid Protocol surface, Base (chain 8453)
/// @notice Signatures verified against Instadapp/fluid-contracts-public sources
///         and deployment artifacts (commit a9949b48). Do not "fix" param names;
///         they match the live contracts.
///
/// Verified Base addresses:
///   Liquidity        0x52Aa899454998Be5b000Ad077a46Bbe360F4e497
///   DexFactory       0x91716C4EDA1Fb55e84Bf8b4c7085f84285c19085
///   fUSDC            0xf42f5795D9ac7e9D757dB633D693cD548Cfd9169
///   fWETH            0x9272D6153133175175Bc276512B2336BE3931CE9
///   DexResolver      0x11D80CfF056Cef4F9E6d23da8672fE9873e5cC07
///   LendingResolver  0x48D32f49aFeAEC7AE66ad7B9264f446fc11a1569

/// @notice Fluid Liquidity layer — the unified liquidity hub every Fluid protocol
///         (fTokens, Vaults, DEXes) settles against.
/// @dev    PROTOCOL-AUTH GATED: only Fluid-listed protocols may call operate().
///         Calling this directly from an unlisted contract reverts. User-level
///         entry points are fToken.deposit / Vault.operate / DEX deposit+borrow.
interface IFluidLiquidity {
    function operate(
        address token_,
        int256 supplyAmount_,   // >0 supply (pulls from supplyTo_), <0 withdraw
        int256 borrowAmount_,   // >0 borrow (sends to borrowTo_), <0 payback
        address supplyTo_,
        address borrowTo_,
        bytes memory callbackData_
    ) external payable returns (bytes memory);
}

/// @notice Fluid DEX factory.
/// @dev    GOVERNANCE GATE (verified in factory main.sol):
///         deployDex reverts with DexFactory__Unauthorized unless
///         isDeployer(msg.sender) — Fluid governance must grant deployer access.
interface IFluidDexFactory {
    function deployDex(address dexDeploymentLogic_, bytes calldata dexDeploymentData_) external returns (address dex_);
    function getDexAddress(uint256 dexId_) external view returns (address);
    function isDeployer(address account_) external view returns (bool);
    function owner() external view returns (address);
}

/// @notice Fluid DEX T1 pool. Col side = "smart collateral" (your deposit IS the
///         LP liquidity and earns swap fees). Debt side = "smart debt" (your debt
///         is deployed as LP liquidity too). Shares are internal balances, not ERC20.
interface IFluidDexT1 {
    // ---- smart collateral ----
    function deposit(uint256 token0Amt_, uint256 token1Amt_, uint256 minSharesAmt_, bool estimate_)
        external payable returns (uint256 shares_);
    function depositPerfect(uint256 shares_, uint256 maxToken0Deposit_, uint256 maxToken1Deposit_, bool estimate_)
        external payable returns (uint256 token0Amt_, uint256 token1Amt_);
    function withdraw(uint256 token0Amt_, uint256 token1Amt_, uint256 maxSharesAmt_, address to_)
        external returns (uint256 shares_);
    function withdrawPerfect(uint256 shares_, uint256 minToken0Withdraw_, uint256 minToken1Withdraw_, address to_)
        external returns (uint256 token0Amt_, uint256 token1Amt_);

    // ---- smart debt ----
    function borrow(uint256 token0Amt_, uint256 token1Amt_, uint256 maxSharesAmt_, address to_)
        external returns (uint256 shares_);
    function borrowPerfect(uint256 shares_, uint256 minToken0Borrow_, uint256 minToken1Borrow_, address to_)
        external returns (uint256 token0Amt_, uint256 token1Amt_);
    function payback(uint256 token0Amt_, uint256 token1Amt_, uint256 minSharesAmt_, bool estimate_)
        external payable returns (uint256 shares_);
    function paybackPerfect(uint256 shares_, uint256 maxToken0Payback_, uint256 maxToken1Payback_, bool estimate_)
        external payable returns (uint256 token0Amt_, uint256 token1Amt_);

    // ---- swaps ----
    function swapIn(bool swap0to1_, uint256 amountIn_, uint256 amountOutMin_, address to_)
        external payable returns (uint256 amountOut_);
    function swapOut(bool swap0to1_, uint256 amountOut_, uint256 amountInMax_, address to_)
        external payable returns (uint256 amountIn_);
}

/// @notice Fluid Vault (T1..T4). VaultT2 = smart collateral (DEX LP) vs normal debt.
///         VaultT4 = smart collateral vs smart debt. operate() is the single entry
///         point for every position: nftId_==0 opens a new position NFT.
interface IFluidVaultT1 {
    function operate(
        uint256 nftId_,      // 0 = new position
        int256 newCol_,      // >0 add collateral, <0 withdraw
        int256 newDebt_,     // >0 borrow, <0 payback
        address to_          // receiver of borrow/withdraw; address(0) = msg.sender
    ) external payable returns (uint256 nftIdOut_, int256, int256);

    function LIQUIDITY() external view returns (address);
    function TYPE() external view returns (uint256);
}

/// @notice Fluid fToken (ERC-4626 lending vault, e.g. fUSDC). Permissionless.
interface IFToken {
    function deposit(uint256 assets_, address receiver_) external returns (uint256 shares_);
    function redeem(uint256 shares_, address receiver_, address owner_) external returns (uint256 assets_);
    function balanceOf(address account_) external view returns (uint256);
    function convertToAssets(uint256 shares_) external view returns (uint256);
    function convertToShares(uint256 assets_) external view returns (uint256);
    function asset() external view returns (address);
}
