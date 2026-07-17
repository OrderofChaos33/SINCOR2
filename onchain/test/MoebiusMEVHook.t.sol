// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {PoolManager} from "v4-core/src/PoolManager.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {Currency, CurrencyLibrary} from "v4-core/src/types/Currency.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {IHooks} from "v4-core/src/interfaces/IHooks.sol";
import {LiquidityAmounts} from "v4-periphery/src/libraries/LiquidityAmounts.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

import {MoebiusMEVHook} from "../src/hooks/MoebiusMEVHook.sol";
import {PhantomCreditToken} from "../src/hooks/PhantomCreditToken.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {MockSearcher} from "./mocks/MockSearcher.sol";

contract MoebiusMEVHookTest is Test {
    using CurrencyLibrary for Currency;

    PoolManager manager;
    PhantomCreditToken pToken;
    MoebiusMEVHook hook;
    MockERC20 real;
    MockSearcher searcher;

    address treasury = makeAddr("treasury");
    address stranger = makeAddr("stranger");

    PoolKey key;
    bool zeroIsPToken;

    uint24 constant DYNAMIC_FEE = 0x800000;
    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;
    uint160 constant SQRT_PRICE_1_1 = 79228162514264337593543950336; // 2**96

    uint256 constant SEED_REAL = 100 ether;
    uint256 constant SEED_PMEV = 100 ether;
    uint256 constant FLASH = 10 ether;
    uint256 constant POLICY_BPS = 100; // 1% floor

    uint160 constant ALL_HOOK_MASK = 0x3FFF;

    function setUp() public {
        manager = new PoolManager(address(this));

        // Circular dependency, production pattern:
        // 1. predict pToken's CREATE address (next nonce of this deployer)
        // 2. mine a CREATE2 salt for the hook whose address has ZERO lower-14
        //    hook-flag bits (required by PoolManager for flag-less hooks)
        // 3. deploy pToken with the mined hook address, then the hook via CREATE2
        address predictedPToken = vm.computeCreateAddress(address(this), vm.getNonce(address(this)));
        bytes32 initHash = keccak256(
            abi.encodePacked(
                type(MoebiusMEVHook).creationCode, abi.encode(address(manager), predictedPToken, treasury, POLICY_BPS)
            )
        );
        bytes32 salt;
        address predictedHook;
        for (uint256 i = 0;; i++) {
            salt = bytes32(i);
            predictedHook = vm.computeCreate2Address(salt, initHash, address(this));
            if (uint160(predictedHook) & ALL_HOOK_MASK == 0) break;
        }

        pToken = new PhantomCreditToken(predictedHook);
        assertEq(address(pToken), predictedPToken, "nonce prediction");
        hook = new MoebiusMEVHook{salt: salt}(IPoolManager(address(manager)), address(pToken), treasury, POLICY_BPS);
        assertEq(address(hook), predictedHook, "CREATE2 prediction");

        real = new MockERC20("Real Asset", "REAL");

        // Sort currencies
        zeroIsPToken = address(pToken) < address(real);
        key = PoolKey({
            currency0: Currency.wrap(zeroIsPToken ? address(pToken) : address(real)),
            currency1: Currency.wrap(zeroIsPToken ? address(real) : address(pToken)),
            fee: DYNAMIC_FEE,
            tickSpacing: TICK_SPACING,
            hooks: IHooks(address(hook))
        });

        manager.initialize(key, SQRT_PRICE_1_1);
        hook.setPoolSwapFee(key, POOL_FEE);

        // Seed the redemption window (central bank balance sheet)
        real.mint(address(this), SEED_REAL * 10);
        real.approve(address(hook), type(uint256).max);

        uint160 sqrtA = TickMath.getSqrtPriceAtTick(-600);
        uint160 sqrtB = TickMath.getSqrtPriceAtTick(600);
        uint256 liquidity = LiquidityAmounts.getLiquidityForAmounts(
            SQRT_PRICE_1_1,
            sqrtA,
            sqrtB,
            zeroIsPToken ? SEED_PMEV : SEED_REAL,
            zeroIsPToken ? SEED_REAL : SEED_PMEV
        );
        hook.seedLiquidity(key, -600, 600, liquidity, SEED_REAL * 2, SEED_PMEV * 2);

        // Reference searcher, pre-funded with "external venue" real-asset profit
        searcher = new MockSearcher(address(manager), address(hook));
        real.mint(address(searcher), 10 ether);
        vm.prank(address(searcher));
        real.approve(address(hook), type(uint256).max);
    }

    function _payload(uint8 mode, uint256 flashAmount) internal view returns (bytes memory) {
        return abi.encode(mode, key, flashAmount, zeroIsPToken);
    }

    // ==================== HAPPY PATH ====================

    function test_fullCycle_bidSplitDeterministically() public {
        uint256 bid = 1 ether;
        uint256 protocolShare = bid / 5; // 20%
        uint256 lpShare = bid - protocolShare;

        uint256 treasuryBefore = real.balanceOf(treasury);
        uint256 pmBefore = real.balanceOf(address(manager));
        uint256 searcherBefore = real.balanceOf(address(searcher));
        uint256 supplyBefore = pToken.totalSupply();

        vm.prank(address(searcher));
        hook.executeMEV(key, FLASH, bid, _payload(1, FLASH));

        // PM gain = LP donation share + the searcher's net swap contribution (fees/impact)
        uint256 searcherSwapCost = (searcherBefore - real.balanceOf(address(searcher))) - bid;
        assertEq(real.balanceOf(treasury) - treasuryBefore, protocolShare, "treasury got 20%");
        assertEq(real.balanceOf(address(manager)) - pmBefore, lpShare + searcherSwapCost, "LPs got 80% + cycle fees");
        assertEq(pToken.totalSupply(), supplyBefore, "pMEV supply back to seeded level");
        assertEq(pToken.balanceOf(address(searcher)), 0, "searcher holds no leftover pMEV");
        assertEq(real.balanceOf(address(hook)), 0, "hook retains nothing");
    }

    function test_holdMode_escrowAndReturn() public {
        uint256 bid = 0.5 ether;
        uint256 pmBefore = real.balanceOf(address(manager));

        vm.prank(address(searcher));
        hook.executeMEV(key, FLASH, bid, _payload(0, FLASH));

        assertEq(real.balanceOf(treasury), bid / 5, "treasury share");
        assertEq(real.balanceOf(address(manager)) - pmBefore, bid - bid / 5, "donation share");
    }

    // ==================== POLICY RATE FLOOR ====================

    function test_bidBelowFloor_reverts() public {
        uint256 floor = (FLASH * POLICY_BPS) / 10_000; // 0.1 ether
        vm.prank(address(searcher));
        vm.expectRevert(abi.encodeWithSelector(MoebiusMEVHook.BidBelowFloor.selector, floor - 1, floor));
        hook.executeMEV(key, FLASH, floor - 1, _payload(0, FLASH));
    }

    function test_policyRate_adminOnly() public {
        vm.prank(stranger);
        vm.expectRevert(MoebiusMEVHook.Unauthorized.selector);
        hook.setMinBidBps(200);

        hook.setMinBidBps(200);
        assertEq(hook.minBidBps(), 200);

        vm.expectRevert(MoebiusMEVHook.RateTooHigh.selector);
        hook.setMinBidBps(1001);
    }

    // ==================== SAFETY ====================

    function test_nonReturningSearcher_entireTxReverts_escrowSafe() public {
        uint256 searcherRealBefore = real.balanceOf(address(searcher));

        vm.prank(address(searcher));
        vm.expectRevert(); // ERC20 burn underflow on repayment
        hook.executeMEV(key, FLASH, 1 ether, _payload(3, FLASH));

        assertEq(real.balanceOf(address(searcher)), searcherRealBefore, "escrow reverted back");
    }

    function test_reentrancy_blocked() public {
        vm.prank(address(searcher));
        vm.expectRevert(); // ReentrancyGuardReentrantCall bubbles up
        hook.executeMEV(key, FLASH, 1 ether, _payload(2, FLASH));
    }

    function test_onlyHookCanMintBurn() public {
        vm.expectRevert(PhantomCreditToken.OnlyHook.selector);
        pToken.mintEphemeral(stranger, 1);
        vm.expectRevert(PhantomCreditToken.OnlyHook.selector);
        pToken.burnEphemeral(stranger, 1);
    }

    function test_wrongPoolKey_reverts() public {
        PoolKey memory badKey = key;
        badKey.hooks = IHooks(stranger);
        vm.prank(address(searcher));
        vm.expectRevert(MoebiusMEVHook.InvalidPoolKey.selector);
        hook.executeMEV(badKey, FLASH, 1 ether, _payload(0, FLASH));
    }

    function test_seededPoolState() public view {
        // Seeded inventory sits in the PoolManager; supply equals absorbed pMEV
        assertGt(pToken.totalSupply(), 0, "pMEV inventory exists");
        assertGt(real.balanceOf(address(manager)), 0, "real asset in pool");
        assertEq(pToken.balanceOf(address(hook)), 0, "hook burned unabsorbed pMEV");
    }

    function test_rescue_ownerOnly() public {
        real.mint(address(hook), 1 ether);
        vm.prank(stranger);
        vm.expectRevert(MoebiusMEVHook.Unauthorized.selector);
        hook.rescue(Currency.wrap(address(real)), 1 ether, stranger);

        hook.rescue(Currency.wrap(address(real)), 1 ether, treasury);
        assertEq(real.balanceOf(treasury), 1 ether);
    }
}
