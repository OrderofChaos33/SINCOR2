// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
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

/// @notice Base mainnet fork test against the REAL canonical PoolManager.
///         Run: forge test --match-contract MoebiusMEVHookFork --fork-url https://mainnet.base.org -vvv
contract MoebiusMEVHookForkTest is Test {
    using CurrencyLibrary for Currency;

    address constant PM_BASE = 0x498581fF718922c3f8e6A244956aF099B2652b2b;

    IPoolManager manager = IPoolManager(PM_BASE);
    PhantomCreditToken pToken;
    MoebiusMEVHook hook;
    MockERC20 real;
    MockSearcher searcher;

    address treasury = makeAddr("treasury");

    PoolKey key;
    bool zeroIsPToken;

    uint24 constant DYNAMIC_FEE = 0x800000;
    uint24 constant POOL_FEE = 3000;
    int24 constant TICK_SPACING = 60;
    uint160 constant SQRT_PRICE_1_1 = 79228162514264337593543950336;

    uint256 constant SEED_REAL = 100 ether;
    uint256 constant SEED_PMEV = 100 ether;
    uint256 constant FLASH = 10 ether;
    uint256 constant POLICY_BPS = 100;

    uint160 constant ALL_HOOK_MASK = 0x3FFF;

    function setUp() public {
        vm.createSelectFork("https://mainnet.base.org");
        require(PM_BASE.code.length > 0, "PoolManager not found on fork");

        // Same CREATE2-mined deploy pattern as production
        address predictedPToken = vm.computeCreateAddress(address(this), vm.getNonce(address(this)));
        bytes32 initHash = keccak256(
            abi.encodePacked(
                type(MoebiusMEVHook).creationCode, abi.encode(PM_BASE, predictedPToken, treasury, POLICY_BPS)
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
        hook = new MoebiusMEVHook{salt: salt}(manager, address(pToken), treasury, POLICY_BPS);
        assertEq(address(hook), predictedHook, "CREATE2 prediction");

        real = new MockERC20("Real Asset", "REAL");

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

        searcher = new MockSearcher(address(manager), address(hook));
        real.mint(address(searcher), 10 ether);
        vm.prank(address(searcher));
        real.approve(address(hook), type(uint256).max);
    }

    function test_fork_fullCycle_onCanonicalPoolManager() public {
        uint256 bid = 1 ether;
        uint256 protocolShare = bid / 5;
        uint256 lpShare = bid - protocolShare;

        uint256 treasuryBefore = real.balanceOf(treasury);
        uint256 pmBefore = real.balanceOf(PM_BASE);
        uint256 searcherBefore = real.balanceOf(address(searcher));
        uint256 supplyBefore = pToken.totalSupply();

        vm.prank(address(searcher));
        hook.executeMEV(key, FLASH, bid, abi.encode(uint8(1), key, FLASH, zeroIsPToken));

        uint256 searcherSwapCost = (searcherBefore - real.balanceOf(address(searcher))) - bid;
        assertEq(real.balanceOf(treasury) - treasuryBefore, protocolShare, "treasury 20%");
        assertEq(real.balanceOf(PM_BASE) - pmBefore, lpShare + searcherSwapCost, "LPs 80% + fees");
        assertEq(pToken.totalSupply(), supplyBefore, "supply restored");
        assertEq(real.balanceOf(address(hook)), 0, "hook retains nothing");
    }

    function test_fork_bidBelowFloor_reverts() public {
        uint256 floor = (FLASH * POLICY_BPS) / 10_000;
        vm.prank(address(searcher));
        vm.expectRevert(abi.encodeWithSelector(MoebiusMEVHook.BidBelowFloor.selector, floor - 1, floor));
        hook.executeMEV(key, FLASH, floor - 1, abi.encode(uint8(0), key, FLASH, zeroIsPToken));
    }

    function test_fork_nonReturningSearcher_atomicRevert() public {
        uint256 searcherBefore = real.balanceOf(address(searcher));
        vm.prank(address(searcher));
        vm.expectRevert();
        hook.executeMEV(key, FLASH, 1 ether, abi.encode(uint8(3), key, FLASH, zeroIsPToken));
        assertEq(real.balanceOf(address(searcher)), searcherBefore, "escrow safe");
    }
}
