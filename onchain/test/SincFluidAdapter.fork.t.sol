// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {SincFluidAdapter} from "../src/fluid/SincFluidAdapter.sol";
import {IFluidDexFactory, IFToken} from "../src/fluid/FluidInterfaces.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @notice Base-mainnet fork test. Proves the live, permissionless leg (fUSDC)
///         end-to-end and documents the governance gate on deployDex.
///         Run: forge test --match-path test/SincFluidAdapter.fork.t.sol \
///                --fork-url $BASE_RPC_URL -vvv
contract SincFluidAdapterForkTest is Test {
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant SINC = 0x9C8cd8d3961F445D653713dE65C6578bE11668e7;
    address constant TREASURY = 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac;
    address constant GUARDIAN = 0xdba7180cdd90D12B9Bc2F15080ddFD9B14fEf31a;
    address constant F_USDC = 0xf42f5795D9ac7e9D757dB633D693cD548Cfd9169;
    address constant DEX_FACTORY = 0x91716C4EDA1Fb55e84Bf8b4c7085f84285c19085;
    address constant LIQUIDITY = 0x52Aa899454998Be5b000Ad077a46Bbe360F4e497;

    SincFluidAdapter adapter;
    address user;

    function setUp() public {
        vm.createSelectFork(vm.envString("BASE_RPC_URL"));
        adapter = new SincFluidAdapter(IERC20(SINC), IERC20(USDC), GUARDIAN, TREASURY);
        user = makeAddr("lp");
    }

    function test_fluidInfraLiveOnBase() public view {
        assertGt(LIQUIDITY.code.length, 0);
        assertGt(DEX_FACTORY.code.length, 0);
        assertGt(F_USDC.code.length, 0);
    }

    /// GOVERNANCE GATE: DexFactory.deployDex reverts for any non-authorized deployer.
    /// Treasury must be granted deployer access by Fluid governance (or Fluid lists
    /// the SINC pair themselves) before Stage 2 can execute.
    function test_deployerGate_blocksTreasury() public view {
        assertFalse(IFluidDexFactory(DEX_FACTORY).isDeployer(TREASURY));
    }

    /// Stage 1 full loop: approve -> depositUSDC -> fUSDC shares -> withdrawUSDC.
    function test_supplyAndWithdraw_fUSDC() public {
        uint256 assets = 100e6; // 100 USDC
        deal(USDC, user, assets);

        vm.startPrank(user);
        IERC20(USDC).approve(address(adapter), assets);
        uint256 shares = adapter.depositUSDC(assets);
        assertGt(shares, 0);
        assertEq(adapter.fUsdcShares(user), shares);
        assertGt(IFToken(F_USDC).balanceOf(address(adapter)), 0);
        assertGe(adapter.userValueUSDC(user), assets - 2); // rounding only

        uint256 balBefore = IERC20(USDC).balanceOf(user);
        uint256 out = adapter.withdrawUSDC(shares);
        vm.stopPrank();

        assertGe(out + 2, assets); // principal back (≤2 wei rounding)
        assertEq(IERC20(USDC).balanceOf(user), balBefore + out);
        assertEq(adapter.fUsdcShares(user), 0);
    }

    /// Stage 2 must revert cleanly until the SINC-USDC DEX exists.
    function test_dexPathsRevertUntilPoolSet() public {
        vm.expectRevert(SincFluidAdapter.DexNotSet.selector);
        adapter.supplyToDex(1e8, 1e6, 0);

        vm.expectRevert(SincFluidAdapter.DexNotSet.selector);
        vm.prank(GUARDIAN);
        adapter.borrowSmartDebt(1, 0, 0, TREASURY);
    }
}
