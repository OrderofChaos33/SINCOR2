// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {StakeSlashManager} from "../contracts/StakeSlashManager.sol";

/// @notice Uniquely-named ERC20 mock (the clamp suite defines its own).
contract SlashMockToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amt) external {
        balanceOf[to] += amt;
    }

    function approve(address spender, uint256 amt) external returns (bool) {
        allowance[msg.sender][spender] = amt;
        return true;
    }

    function transfer(address to, uint256 amt) external returns (bool) {
        _move(msg.sender, to, amt);
        return true;
    }

    function transferFrom(address from, address to, uint256 amt) external returns (bool) {
        uint256 a = allowance[from][msg.sender];
        require(a >= amt, "allowance");
        if (a != type(uint256).max) allowance[from][msg.sender] = a - amt;
        _move(from, to, amt);
        return true;
    }

    function _move(address from, address to, uint256 amt) internal {
        require(balanceOf[from] >= amt, "balance");
        balanceOf[from] -= amt;
        balanceOf[to] += amt;
    }
}

/**
 * @notice Dedicated StakeSlashManager suite (wave 2 blind-spot remediation).
 *         Extends -- does NOT duplicate -- the 5 W-33 clamp tests in
 *         StakeSlashManagerUnstakeClamp.t.sol: this file covers the stake
 *         lifecycle, min-stake rules, request/finalize timing edges, slash
 *         signature authorization (valid / wrong signer / malleable /
 *         replay / expired / bad nonce / bad reason / amount bounds), the
 *         treasury-first split with poster re-auction credits, and the
 *         admin/adjudicator role boundaries.
 *
 *         Conservation invariant asserted throughout: token balance of the
 *         manager == sum(stakeOf) + sum(posterReAuctionCredits) for the
 *         actors in play (credits are 1:1 token-backed, held in-contract).
 */
contract StakeSlashManagerTest is Test {
    event Staked(address indexed agent, uint256 amount);
    event UnstakeRequested(address indexed agent, uint256 amount, uint256 readyAt);
    event SlashExecuted(
        address indexed agent,
        address indexed poster,
        uint256 amount,
        uint256 treasuryCut,
        uint64 nonce,
        bytes32 reason
    );
    event AdjudicatorRotated(address indexed oldAdjudicator, address indexed newAdjudicator);
    event MinStakeUpdated(uint256 minStakeWei);

    uint256 internal constant ADJ_PK = 0xA11CE;
    uint256 internal constant WRONG_PK = 0xBAD;
    uint256 internal constant N_ORDER =
        0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;

    SlashMockToken public token;
    StakeSlashManager public mgr;

    address public adjudicator;
    address public agent = address(0xA6E77);
    address public agent2 = address(0xA6E78);
    address public poster = address(0xB055);
    address public treasury = address(0x7EA5);
    address public stranger = address(0x57AA);

    uint256 internal constant STAKE = 1000 ether;
    uint256 internal constant MIN_STAKE = 100 ether;
    uint256 internal constant TIMELOCK = 7 days;
    uint256 internal constant T0 = 1_700_000_000;

    bytes32 internal constant RULING_TYPEHASH = keccak256("SINCOR-SLASH");

    // Cached in setUp: reading mgr.REASON_*() inline after vm.expectRevert
    // would consume the expectation with the getter call instead.
    bytes32 internal reasonGhost;
    bytes32 internal reasonQuality;

    function setUp() public {
        vm.warp(T0);
        token = new SlashMockToken();
        adjudicator = vm.addr(ADJ_PK);
        mgr = new StakeSlashManager(
            address(this), // admin
            adjudicator,
            address(token),
            treasury,
            MIN_STAKE,
            TIMELOCK
        );
        reasonGhost = mgr.REASON_GHOST();
        reasonQuality = mgr.REASON_QUALITY();
        token.mint(agent, 100_000 ether);
        token.mint(agent2, 100_000 ether);
        vm.prank(agent);
        token.approve(address(mgr), type(uint256).max);
        vm.prank(agent2);
        token.approve(address(mgr), type(uint256).max);
        vm.prank(agent);
        mgr.stake(STAKE);
    }

    // --- helpers ----------------------------------------------------------

    function _sign(
        uint256 pk,
        address target,
        address pstr,
        uint256 amount,
        uint256 cut,
        uint64 nonce,
        uint64 expiry,
        bytes32 reason
    ) internal view returns (uint8 v, bytes32 r, bytes32 s) {
        bytes32 structHash = keccak256(
            abi.encode(
                RULING_TYPEHASH,
                block.chainid,
                address(mgr),
                target,
                pstr,
                amount,
                cut,
                nonce,
                expiry,
                reason
            )
        );
        bytes32 ethSigned = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", structHash)
        );
        return vm.sign(pk, ethSigned);
    }

    function _slashSigned(
        uint256 pk,
        address target,
        address pstr,
        uint256 amount,
        uint256 cut,
        uint64 nonce,
        uint64 expiry,
        bytes32 reason
    ) internal {
        (uint8 v, bytes32 r, bytes32 s) = _sign(pk, target, pstr, amount, cut, nonce, expiry, reason);
        mgr.slash(target, pstr, amount, cut, nonce, expiry, reason, v, r, s);
    }

    /// @dev Slash with a fresh adjudicator signature at the current nonce.
    function _slash(uint256 amount, uint256 cut) internal {
        _slashSigned(
            ADJ_PK,
            agent,
            poster,
            amount,
            cut,
            mgr.slashNonceOf(agent),
            uint64(block.timestamp + 1 days),
            reasonQuality
        );
    }

    function _conservation() internal view {
        assertEq(
            token.balanceOf(address(mgr)),
            mgr.stakeOf(agent) +
                mgr.stakeOf(agent2) +
                mgr.posterReAuctionCredits(poster),
            "manager balance == stakes + poster credits"
        );
    }

    // --- constructor / config ----------------------------------------------

    function test_Constructor_ZeroAddressGuards() public {
        vm.expectRevert(StakeSlashManager.InvalidConfig.selector);
        new StakeSlashManager(address(0), adjudicator, address(token), treasury, MIN_STAKE, TIMELOCK);
        vm.expectRevert(StakeSlashManager.InvalidConfig.selector);
        new StakeSlashManager(address(this), address(0), address(token), treasury, MIN_STAKE, TIMELOCK);
        vm.expectRevert(StakeSlashManager.InvalidConfig.selector);
        new StakeSlashManager(address(this), adjudicator, address(0), treasury, MIN_STAKE, TIMELOCK);
        vm.expectRevert(StakeSlashManager.InvalidConfig.selector);
        new StakeSlashManager(address(this), adjudicator, address(token), address(0), MIN_STAKE, TIMELOCK);
    }

    function test_Constructor_ZeroTimelockDefaultsToSevenDays() public {
        StakeSlashManager m2 = new StakeSlashManager(
            address(this), adjudicator, address(token), treasury, MIN_STAKE, 0
        );
        assertEq(m2.unstakeTimelock(), 7 days);
        assertEq(mgr.unstakeTimelock(), TIMELOCK);
    }

    // --- staking -------------------------------------------------------------

    function test_Stake_ZeroAmountReverts() public {
        vm.prank(agent);
        vm.expectRevert(StakeSlashManager.InvalidAmount.selector);
        mgr.stake(0);
    }

    function test_Stake_AccumulatesAndConserves() public {
        vm.expectEmit(true, false, false, true);
        emit Staked(agent, 250 ether);
        vm.prank(agent);
        mgr.stake(250 ether);
        assertEq(mgr.stakeOf(agent), STAKE + 250 ether);
        _conservation();
    }

    function testFuzz_Stake_TwoAgentsConserve(uint256 a1, uint256 a2) public {
        a1 = bound(a1, 1, 5_000 ether);
        a2 = bound(a2, 1, 5_000 ether);
        vm.prank(agent);
        mgr.stake(a1);
        vm.prank(agent2);
        mgr.stake(a2);
        assertEq(mgr.stakeOf(agent), STAKE + a1);
        assertEq(mgr.stakeOf(agent2), a2);
        _conservation();
    }

    function test_CanParticipate_Threshold() public {
        assertTrue(mgr.canParticipate(agent), "at/above min");
        assertFalse(mgr.canParticipate(agent2), "no stake yet");
        vm.prank(agent2);
        mgr.stake(MIN_STAKE - 1);
        assertFalse(mgr.canParticipate(agent2), "1 wei below min");
        vm.prank(agent2);
        mgr.stake(1);
        assertTrue(mgr.canParticipate(agent2), "exactly at min");
    }

    // --- requestUnstake -------------------------------------------------------

    function test_RequestUnstake_ZeroOrOverBalanceReverts() public {
        vm.prank(agent);
        vm.expectRevert(StakeSlashManager.InsufficientStake.selector);
        mgr.requestUnstake(0);
        vm.prank(agent);
        vm.expectRevert(StakeSlashManager.InsufficientStake.selector);
        mgr.requestUnstake(STAKE + 1);
    }

    function test_RequestUnstake_CannotDipBelowMinUnlessFullExit() public {
        vm.prank(agent);
        vm.expectRevert(StakeSlashManager.InsufficientStake.selector);
        mgr.requestUnstake(STAKE - MIN_STAKE + 1); // would leave MIN-1

        vm.prank(agent);
        mgr.requestUnstake(STAKE - MIN_STAKE); // leaves exactly MIN: ok
        assertEq(mgr.unstakePending(agent), STAKE - MIN_STAKE);
    }

    function test_RequestUnstake_SetsPendingAndReadyAt() public {
        vm.expectEmit(true, false, false, true);
        emit UnstakeRequested(agent, 400 ether, T0 + TIMELOCK);
        vm.prank(agent);
        mgr.requestUnstake(400 ether);
        assertEq(mgr.unstakePending(agent), 400 ether);
        assertEq(mgr.unstakeReadyAt(agent), T0 + TIMELOCK);
        // Stake is NOT moved at request time.
        assertEq(mgr.stakeOf(agent), STAKE);
        _conservation();
    }

    function test_RequestUnstake_SecondRequestOverwritesAndResetsClock() public {
        vm.prank(agent);
        mgr.requestUnstake(300 ether);
        vm.warp(T0 + 3 days);
        vm.prank(agent);
        mgr.requestUnstake(200 ether);
        assertEq(mgr.unstakePending(agent), 200 ether, "pending replaced");
        assertEq(
            mgr.unstakeReadyAt(agent),
            T0 + 3 days + TIMELOCK,
            "timelock restarts on overwrite"
        );
    }

    // --- finalizeUnstake timing (clamp behavior lives in the clamp suite) -----

    function test_Finalize_NoPendingReverts() public {
        vm.prank(agent);
        vm.expectRevert(StakeSlashManager.NoPendingUnstake.selector);
        mgr.finalizeUnstake();
    }

    function test_Finalize_TooEarlyRevertsWithReadyAt() public {
        vm.prank(agent);
        mgr.requestUnstake(400 ether);
        uint256 readyAt = mgr.unstakeReadyAt(agent);
        vm.warp(readyAt - 1);
        vm.prank(agent);
        vm.expectRevert(
            abi.encodeWithSelector(
                StakeSlashManager.TimelockNotElapsed.selector, readyAt, readyAt - 1
            )
        );
        mgr.finalizeUnstake();
    }

    function test_Finalize_AtExactReadyAt_Succeeds_PartialLifecycle() public {
        vm.prank(agent);
        mgr.requestUnstake(400 ether);
        vm.warp(mgr.unstakeReadyAt(agent)); // exactly at the boundary

        uint256 balBefore = token.balanceOf(agent);
        vm.prank(agent);
        mgr.finalizeUnstake();

        assertEq(token.balanceOf(agent) - balBefore, 400 ether);
        assertEq(mgr.stakeOf(agent), STAKE - 400 ether, "remainder stays staked");
        assertTrue(mgr.canParticipate(agent));
        assertEq(mgr.unstakePending(agent), 0);
        assertEq(mgr.unstakeReadyAt(agent), 0);
        _conservation();
    }

    function testFuzz_UnstakeLifecycle(uint256 stakeAmt, uint256 unstakeAmt) public {
        stakeAmt = bound(stakeAmt, MIN_STAKE, 5_000 ether);
        unstakeAmt = bound(unstakeAmt, 1, stakeAmt);
        // A partial unstake must leave >= min unless it is a full exit.
        vm.assume(unstakeAmt == stakeAmt || stakeAmt - unstakeAmt >= MIN_STAKE);

        token.mint(stranger, stakeAmt);
        vm.startPrank(stranger);
        token.approve(address(mgr), type(uint256).max);
        mgr.stake(stakeAmt);
        mgr.requestUnstake(unstakeAmt);
        vm.warp(mgr.unstakeReadyAt(stranger));
        uint256 balBefore = token.balanceOf(stranger);
        mgr.finalizeUnstake();
        vm.stopPrank();

        assertEq(token.balanceOf(stranger) - balBefore, unstakeAmt);
        assertEq(mgr.stakeOf(stranger), stakeAmt - unstakeAmt);
        assertEq(mgr.unstakePending(stranger), 0);
    }

    // --- slash: authorization & validation ------------------------------------

    function test_Slash_ValidRuling_SplitsTreasuryFirstThenCredit() public {
        uint256 treasuryBefore = token.balanceOf(treasury);
        vm.expectEmit(true, true, false, true);
        emit SlashExecuted(agent, poster, 400 ether, 150 ether, 0, reasonQuality);
        _slash(400 ether, 150 ether);

        assertEq(mgr.stakeOf(agent), STAKE - 400 ether);
        assertEq(token.balanceOf(treasury) - treasuryBefore, 150 ether, "treasury cut paid out");
        assertEq(mgr.posterReAuctionCredits(poster), 250 ether, "remainder is poster credit");
        assertEq(mgr.slashNonceOf(agent), 1, "nonce consumed");
        _conservation();
    }

    function test_Slash_FullCutToTreasury_NoCredit() public {
        _slash(300 ether, 300 ether);
        assertEq(mgr.posterReAuctionCredits(poster), 0);
        assertEq(token.balanceOf(treasury), 300 ether);
        _conservation();
    }

    function test_Slash_ZeroCut_AllCredit() public {
        _slash(300 ether, 0);
        assertEq(mgr.posterReAuctionCredits(poster), 300 ether);
        assertEq(token.balanceOf(treasury), 0);
        _conservation();
    }

    function test_Slash_WrongSignerReverts() public {
        uint64 nonce = mgr.slashNonceOf(agent);
        uint64 expiry = uint64(block.timestamp + 1 days);
        (uint8 v, bytes32 r, bytes32 s) = _sign(
            WRONG_PK, agent, poster, 100 ether, 0, nonce, expiry, reasonGhost
        );
        vm.expectRevert(StakeSlashManager.BadSignature.selector);
        mgr.slash(agent, poster, 100 ether, 0, nonce, expiry, reasonGhost, v, r, s);
        assertEq(mgr.stakeOf(agent), STAKE, "stake untouched");
        assertEq(mgr.slashNonceOf(agent), 0, "nonce not consumed");
    }

    function test_Slash_MalleableSignatureReverts() public {
        uint64 nonce = mgr.slashNonceOf(agent);
        uint64 expiry = uint64(block.timestamp + 1 days);
        (uint8 v, bytes32 r, bytes32 s) = _sign(
            ADJ_PK, agent, poster, 100 ether, 0, nonce, expiry, reasonGhost
        );
        bytes32 sMalleable = bytes32(N_ORDER - uint256(s));
        uint8 vFlipped = v == 27 ? 28 : 27;
        vm.expectRevert(StakeSlashManager.BadSignature.selector);
        mgr.slash(agent, poster, 100 ether, 0, nonce, expiry, reasonGhost, vFlipped, r, sMalleable);
    }

    function test_Slash_InvalidVReverts() public {
        uint64 nonce = mgr.slashNonceOf(agent);
        uint64 expiry = uint64(block.timestamp + 1 days);
        (, bytes32 r, bytes32 s) = _sign(
            ADJ_PK, agent, poster, 100 ether, 0, nonce, expiry, reasonGhost
        );
        vm.expectRevert(StakeSlashManager.BadSignature.selector);
        mgr.slash(agent, poster, 100 ether, 0, nonce, expiry, reasonGhost, 29, r, s);
    }

    function test_Slash_ReplayedRulingRevertsBadNonce() public {
        uint64 nonce = mgr.slashNonceOf(agent);
        uint64 expiry = uint64(block.timestamp + 1 days);
        (uint8 v, bytes32 r, bytes32 s) = _sign(
            ADJ_PK, agent, poster, 100 ether, 0, nonce, expiry, reasonGhost
        );
        mgr.slash(agent, poster, 100 ether, 0, nonce, expiry, reasonGhost, v, r, s);
        // Verbatim replay: signature is genuine, but the nonce is spent.
        vm.expectRevert(
            abi.encodeWithSelector(StakeSlashManager.BadNonce.selector, uint256(1), uint256(0))
        );
        mgr.slash(agent, poster, 100 ether, 0, nonce, expiry, reasonGhost, v, r, s);
    }

    function test_Slash_FutureNonceRevertsBadNonce() public {
        uint64 expiry = uint64(block.timestamp + 1 days);
        (uint8 v, bytes32 r, bytes32 s) = _sign(
            ADJ_PK, agent, poster, 100 ether, 0, 5, expiry, reasonGhost
        );
        vm.expectRevert(
            abi.encodeWithSelector(StakeSlashManager.BadNonce.selector, uint256(0), uint256(5))
        );
        mgr.slash(agent, poster, 100 ether, 0, 5, expiry, reasonGhost, v, r, s);
    }

    function test_Slash_ExpiredRulingReverts() public {
        uint64 nonce = mgr.slashNonceOf(agent);
        uint64 expiry = uint64(block.timestamp - 1);
        (uint8 v, bytes32 r, bytes32 s) = _sign(
            ADJ_PK, agent, poster, 100 ether, 0, nonce, expiry, reasonGhost
        );
        vm.expectRevert(
            abi.encodeWithSelector(
                StakeSlashManager.Expired.selector, uint256(expiry), block.timestamp
            )
        );
        mgr.slash(agent, poster, 100 ether, 0, nonce, expiry, reasonGhost, v, r, s);
    }

    function test_Slash_AtExactExpiry_Succeeds() public {
        // Revert condition is block.timestamp > expiry: expiry == now is valid.
        _slashSigned(
            ADJ_PK, agent, poster, 100 ether, 0,
            mgr.slashNonceOf(agent), uint64(block.timestamp), reasonGhost
        );
        assertEq(mgr.stakeOf(agent), STAKE - 100 ether);
    }

    function test_Slash_BadReasonReverts() public {
        uint64 nonce = mgr.slashNonceOf(agent);
        uint64 expiry = uint64(block.timestamp + 1 days);
        bytes32 bogus = keccak256("bogus");
        (uint8 v, bytes32 r, bytes32 s) = _sign(
            ADJ_PK, agent, poster, 100 ether, 0, nonce, expiry, bogus
        );
        vm.expectRevert(StakeSlashManager.BadReason.selector);
        mgr.slash(agent, poster, 100 ether, 0, nonce, expiry, bogus, v, r, s);
    }

    function test_Slash_AmountBounds() public {
        uint64 nonce = mgr.slashNonceOf(agent);
        uint64 expiry = uint64(block.timestamp + 1 days);
        (uint8 v, bytes32 r, bytes32 s) = _sign(
            ADJ_PK, agent, poster, 0, 0, nonce, expiry, reasonGhost
        );
        // amount == 0
        vm.expectRevert(StakeSlashManager.InsufficientStake.selector);
        mgr.slash(agent, poster, 0, 0, nonce, expiry, reasonGhost, v, r, s);
        // amount > stake
        (v, r, s) = _sign(ADJ_PK, agent, poster, STAKE + 1, 0, nonce, expiry, reasonGhost);
        vm.expectRevert(StakeSlashManager.InsufficientStake.selector);
        mgr.slash(agent, poster, STAKE + 1, 0, nonce, expiry, reasonGhost, v, r, s);
        // treasuryCut > amount
        (v, r, s) = _sign(ADJ_PK, agent, poster, 100 ether, 100 ether + 1, nonce, expiry, reasonGhost);
        vm.expectRevert(StakeSlashManager.InvalidAmount.selector);
        mgr.slash(agent, poster, 100 ether, 100 ether + 1, nonce, expiry, reasonGhost, v, r, s);
        assertEq(mgr.stakeOf(agent), STAKE, "nothing moved");
    }

    function test_Slash_ZeroAddressesRevert() public {
        uint64 expiry = uint64(block.timestamp + 1 days);
        vm.expectRevert(StakeSlashManager.InvalidConfig.selector);
        mgr.slash(address(0), poster, 1, 0, 0, expiry, reasonGhost, 27, bytes32(uint256(1)), bytes32(uint256(1)));
        vm.expectRevert(StakeSlashManager.InvalidConfig.selector);
        mgr.slash(agent, address(0), 1, 0, 0, expiry, reasonGhost, 27, bytes32(uint256(1)), bytes32(uint256(1)));
    }

    function test_Slash_SequentialRulingsConsumeSequentialNonces() public {
        _slash(100 ether, 0);
        _slash(100 ether, 0);
        _slash(100 ether, 0);
        assertEq(mgr.slashNonceOf(agent), 3);
        assertEq(mgr.stakeOf(agent), STAKE - 300 ether);
        assertEq(mgr.posterReAuctionCredits(poster), 300 ether);
        _conservation();
    }

    function testFuzz_Slash_SplitAlwaysConserves(uint256 amount, uint256 cut) public {
        amount = bound(amount, 1, STAKE);
        cut = bound(cut, 0, amount);
        uint256 treasuryBefore = token.balanceOf(treasury);
        _slash(amount, cut);
        assertEq(mgr.stakeOf(agent), STAKE - amount);
        assertEq(mgr.posterReAuctionCredits(poster), amount - cut);
        assertEq(token.balanceOf(treasury) - treasuryBefore, cut);
        _conservation();
    }

    function testFuzz_Slash_TamperedAmountBreaksSignature(uint256 amount, uint256 tamper) public {
        amount = bound(amount, 1, STAKE - 1);
        tamper = bound(tamper, 1, STAKE - amount);
        uint64 nonce = mgr.slashNonceOf(agent);
        uint64 expiry = uint64(block.timestamp + 1 days);
        (uint8 v, bytes32 r, bytes32 s) = _sign(
            ADJ_PK, agent, poster, amount, 0, nonce, expiry, reasonQuality
        );
        // Signature was over `amount`; submitting amount+tamper must fail.
        vm.expectRevert(StakeSlashManager.BadSignature.selector);
        mgr.slash(agent, poster, amount + tamper, 0, nonce, expiry, reasonQuality, v, r, s);
    }

    // --- role boundaries --------------------------------------------------------

    function test_SetAdjudicator_OnlyCurrentAdjudicator() public {
        address newAdj = vm.addr(0xBEEF);
        // Admin (this contract) is NOT the adjudicator: rotation is not an
        // admin power in this contract.
        vm.expectRevert(StakeSlashManager.Unauthorized.selector);
        mgr.setAdjudicator(newAdj);
        vm.prank(stranger);
        vm.expectRevert(StakeSlashManager.Unauthorized.selector);
        mgr.setAdjudicator(newAdj);

        vm.expectEmit(true, false, false, true);
        emit AdjudicatorRotated(adjudicator, newAdj);
        vm.prank(adjudicator);
        mgr.setAdjudicator(newAdj);
        assertEq(mgr.adjudicator(), newAdj);
    }

    function test_SetAdjudicator_ZeroReverts() public {
        vm.prank(adjudicator);
        vm.expectRevert(StakeSlashManager.InvalidConfig.selector);
        mgr.setAdjudicator(address(0));
    }

    function test_SetAdjudicator_RotationInvalidatesOldSigner() public {
        uint256 newPk = 0xBEEF;
        address newAdj = vm.addr(newPk);
        vm.prank(adjudicator);
        mgr.setAdjudicator(newAdj);

        // Old adjudicator's ruling no longer verifies.
        uint64 nonce = mgr.slashNonceOf(agent);
        uint64 expiry = uint64(block.timestamp + 1 days);
        (uint8 v, bytes32 r, bytes32 s) = _sign(
            ADJ_PK, agent, poster, 100 ether, 0, nonce, expiry, reasonQuality
        );
        vm.expectRevert(StakeSlashManager.BadSignature.selector);
        mgr.slash(agent, poster, 100 ether, 0, nonce, expiry, reasonQuality, v, r, s);
        // New adjudicator's ruling works.
        _slashSigned(
            newPk, agent, poster, 100 ether, 0,
            mgr.slashNonceOf(agent), uint64(block.timestamp + 1 days), reasonGhost
        );
        assertEq(mgr.stakeOf(agent), STAKE - 100 ether);
    }

    function test_SetMinStakeWei_AdminOnly() public {
        vm.prank(stranger);
        vm.expectRevert(StakeSlashManager.Unauthorized.selector);
        mgr.setMinStakeWei(1 ether);

        vm.expectEmit(false, false, false, true);
        emit MinStakeUpdated(50 ether);
        mgr.setMinStakeWei(50 ether); // admin = address(this)
        assertEq(mgr.minStakeWei(), 50 ether);
        // agent2 with 75 ether now participates; before the change it could not.
        vm.prank(agent2);
        mgr.stake(75 ether);
        assertTrue(mgr.canParticipate(agent2));
    }
}
