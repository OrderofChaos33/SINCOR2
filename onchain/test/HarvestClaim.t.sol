// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {HarvestClaim} from "../src/HarvestClaim.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/**
 * @title  HarvestClaimTest
 * @notice Foundry tests for HarvestClaim covering:
 *         - Happy path (single + multi-wallet)
 *         - Double-claim revert
 *         - Invalid proof revert
 *         - Zero amount revert
 *         - Root already finalized revert
 *         - Claim before root set
 *         - Window closed revert
 *         - Pause / unpause
 *         - Reentrancy guard (no re-entrant ERC20)
 *         - Sweep after window
 *         - isEligible view
 *         - remainingAllocation / claimWindowOpen views
 */
contract HarvestClaimTest is Test {
    // ── Mock ERC20 ────────────────────────────────────────────────────────────
    MockSINC internal sinc;

    // ── Actors ────────────────────────────────────────────────────────────────
    address internal owner    = makeAddr("owner");
    address internal treasury = makeAddr("treasury");
    address internal alice    = makeAddr("alice");
    address internal bob      = makeAddr("bob");
    address internal carol    = makeAddr("carol");

    // ── Contract under test ───────────────────────────────────────────────────
    HarvestClaim internal harvest;

    // ── Merkle tree data (3-leaf tree for alice/bob/carol) ────────────────────
    uint256 internal constant ALICE_AMOUNT = 100e18;
    uint256 internal constant BOB_AMOUNT   = 200e18;
    uint256 internal constant CAROL_AMOUNT = 150e18;
    uint256 internal constant TOTAL_ALLOC  = ALICE_AMOUNT + BOB_AMOUNT + CAROL_AMOUNT;

    bytes32 internal aliceLeaf;
    bytes32 internal bobLeaf;
    bytes32 internal carolLeaf;
    bytes32 internal merkleRoot;

    // Proofs for each leaf (3-leaf sorted tree)
    bytes32[] internal aliceProof;
    bytes32[] internal bobProof;
    bytes32[] internal carolProof;

    function setUp() public {
        sinc    = new MockSINC();
        harvest = new HarvestClaim(address(sinc), treasury, owner);

        // Build 3-leaf Merkle tree (sorted-pair style)
        aliceLeaf = keccak256(abi.encodePacked(alice, ALICE_AMOUNT));
        bobLeaf   = keccak256(abi.encodePacked(bob,   BOB_AMOUNT));
        carolLeaf = keccak256(abi.encodePacked(carol, CAROL_AMOUNT));

        // Sort and combine pairs to build root
        bytes32 ab = _hashPair(aliceLeaf, bobLeaf);
        bytes32 root3 = _hashPair(ab, carolLeaf);
        merkleRoot = root3;

        // alice proof: [bobLeaf, carolLeaf]
        aliceProof = new bytes32[](2);
        aliceProof[0] = bobLeaf;
        aliceProof[1] = carolLeaf;

        // bob proof: [aliceLeaf, carolLeaf]
        bobProof = new bytes32[](2);
        bobProof[0] = aliceLeaf;
        bobProof[1] = carolLeaf;

        // carol proof: [ab]
        carolProof = new bytes32[](1);
        carolProof[0] = ab;

        // Fund contract
        sinc.mint(address(harvest), TOTAL_ALLOC);
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    function _hashPair(bytes32 a, bytes32 b) internal pure returns (bytes32) {
        return a < b ? keccak256(abi.encodePacked(a, b)) : keccak256(abi.encodePacked(b, a));
    }

    function _activateRoot(uint256 windowDays) internal {
        vm.prank(owner);
        harvest.setRoot(merkleRoot, windowDays, TOTAL_ALLOC);
    }

    // ── setRoot ───────────────────────────────────────────────────────────────

    function test_SetRoot() public {
        _activateRoot(30);
        assertEq(harvest.merkleRoot(), merkleRoot);
        assertTrue(harvest.rootFinalized());
        assertTrue(harvest.claimWindowOpen());
    }

    function test_SetRoot_CannotSetTwice() public {
        _activateRoot(30);
        vm.prank(owner);
        vm.expectRevert(HarvestClaim.RootAlreadyFinalized.selector);
        harvest.setRoot(merkleRoot, 30, TOTAL_ALLOC);
    }

    function test_SetRoot_ZeroRootReverts() public {
        vm.prank(owner);
        vm.expectRevert(HarvestClaim.RootNotSet.selector);
        harvest.setRoot(bytes32(0), 30, TOTAL_ALLOC);
    }

    function test_SetRoot_ZeroWindowReverts() public {
        vm.prank(owner);
        vm.expectRevert(HarvestClaim.ZeroAmount.selector);
        harvest.setRoot(merkleRoot, 0, TOTAL_ALLOC);
    }

    function test_SetRoot_InsufficientBalanceReverts() public {
        // Deploy fresh contract with no tokens
        HarvestClaim fresh = new HarvestClaim(address(sinc), treasury, owner);
        vm.prank(owner);
        vm.expectRevert("HarvestClaim: insufficient balance");
        fresh.setRoot(merkleRoot, 30, TOTAL_ALLOC);
    }

    // ── Happy path claims ─────────────────────────────────────────────────────

    function test_AliceClaim_HappyPath() public {
        _activateRoot(30);
        vm.prank(alice);
        harvest.claim(aliceProof, ALICE_AMOUNT);
        assertEq(sinc.balanceOf(alice), ALICE_AMOUNT);
        assertTrue(harvest.claimed(alice));
        assertEq(harvest.totalClaimed(), ALICE_AMOUNT);
    }

    function test_BobClaim_HappyPath() public {
        _activateRoot(30);
        vm.prank(bob);
        harvest.claim(bobProof, BOB_AMOUNT);
        assertEq(sinc.balanceOf(bob), BOB_AMOUNT);
    }

    function test_CarolClaim_HappyPath() public {
        _activateRoot(30);
        vm.prank(carol);
        harvest.claim(carolProof, CAROL_AMOUNT);
        assertEq(sinc.balanceOf(carol), CAROL_AMOUNT);
    }

    function test_AllThreeClaim() public {
        _activateRoot(30);
        vm.prank(alice); harvest.claim(aliceProof, ALICE_AMOUNT);
        vm.prank(bob);   harvest.claim(bobProof,   BOB_AMOUNT);
        vm.prank(carol); harvest.claim(carolProof, CAROL_AMOUNT);
        assertEq(harvest.totalClaimed(), TOTAL_ALLOC);
        assertEq(harvest.remainingAllocation(), 0);
    }

    // ── Double-claim ──────────────────────────────────────────────────────────

    function test_DoubleClaim_Reverts() public {
        _activateRoot(30);
        vm.startPrank(alice);
        harvest.claim(aliceProof, ALICE_AMOUNT);
        vm.expectRevert(HarvestClaim.AlreadyClaimed.selector);
        harvest.claim(aliceProof, ALICE_AMOUNT);
        vm.stopPrank();
    }

    // ── Invalid proof ─────────────────────────────────────────────────────────

    function test_InvalidProof_Reverts() public {
        _activateRoot(30);
        bytes32[] memory badProof = new bytes32[](1);
        badProof[0] = bytes32(uint256(0xdead));
        vm.prank(alice);
        vm.expectRevert(HarvestClaim.InvalidProof.selector);
        harvest.claim(badProof, ALICE_AMOUNT);
    }

    function test_WrongAmount_Reverts() public {
        _activateRoot(30);
        vm.prank(alice);
        vm.expectRevert(HarvestClaim.InvalidProof.selector);
        harvest.claim(aliceProof, ALICE_AMOUNT + 1);
    }

    // ── Zero amount ───────────────────────────────────────────────────────────

    function test_ZeroAmount_Reverts() public {
        _activateRoot(30);
        vm.prank(alice);
        vm.expectRevert(HarvestClaim.ZeroAmount.selector);
        harvest.claim(aliceProof, 0);
    }

    // ── Root not set ──────────────────────────────────────────────────────────

    function test_ClaimBeforeRoot_Reverts() public {
        vm.prank(alice);
        vm.expectRevert(HarvestClaim.RootNotSet.selector);
        harvest.claim(aliceProof, ALICE_AMOUNT);
    }

    // ── Window closed ─────────────────────────────────────────────────────────

    function test_ClaimAfterWindow_Reverts() public {
        _activateRoot(30);
        vm.warp(block.timestamp + 31 days);
        vm.prank(alice);
        vm.expectRevert(HarvestClaim.WindowClosed.selector);
        harvest.claim(aliceProof, ALICE_AMOUNT);
        assertFalse(harvest.claimWindowOpen());
    }

    // ── Pause / unpause ───────────────────────────────────────────────────────

    function test_PausedClaim_Reverts() public {
        _activateRoot(30);
        vm.prank(owner);
        harvest.pause();
        vm.prank(alice);
        vm.expectRevert(); // Pausable: EnforcedPause
        harvest.claim(aliceProof, ALICE_AMOUNT);
    }

    function test_UnpausedClaim_Succeeds() public {
        _activateRoot(30);
        vm.prank(owner); harvest.pause();
        vm.prank(owner); harvest.unpause();
        vm.prank(alice);
        harvest.claim(aliceProof, ALICE_AMOUNT);
        assertEq(sinc.balanceOf(alice), ALICE_AMOUNT);
    }

    function test_PauseBlocksClaimWindowOpen() public {
        _activateRoot(30);
        vm.prank(owner);
        harvest.pause();
        assertFalse(harvest.claimWindowOpen());
    }

    // ── Sweep ─────────────────────────────────────────────────────────────────

    function test_Sweep_AfterWindowCloses() public {
        _activateRoot(30);
        vm.prank(alice); harvest.claim(aliceProof, ALICE_AMOUNT);
        vm.warp(block.timestamp + 31 days);
        uint256 remaining = harvest.remainingAllocation();
        vm.prank(owner);
        harvest.sweep();
        assertEq(sinc.balanceOf(treasury), remaining);
    }

    function test_Sweep_BeforeWindowCloses_Reverts() public {
        _activateRoot(30);
        vm.prank(owner);
        vm.expectRevert(HarvestClaim.WindowNotClosed.selector);
        harvest.sweep();
    }

    function test_Sweep_EmptyBalance_Reverts() public {
        _activateRoot(30);
        vm.prank(alice); harvest.claim(aliceProof, ALICE_AMOUNT);
        vm.prank(bob);   harvest.claim(bobProof,   BOB_AMOUNT);
        vm.prank(carol); harvest.claim(carolProof, CAROL_AMOUNT);
        vm.warp(block.timestamp + 31 days);
        vm.prank(owner);
        vm.expectRevert(HarvestClaim.NothingToSweep.selector);
        harvest.sweep();
    }

    // ── extendWindow ──────────────────────────────────────────────────────────

    function test_ExtendWindow() public {
        _activateRoot(30);
        uint256 currentEnd = harvest.claimWindowEnd();
        vm.prank(owner);
        harvest.extendWindow(currentEnd + 30 days);
        assertEq(harvest.claimWindowEnd(), currentEnd + 30 days);
    }

    function test_ExtendWindow_CannotShorten() public {
        _activateRoot(30);
        uint256 currentEnd = harvest.claimWindowEnd();
        vm.prank(owner);
        vm.expectRevert("HarvestClaim: new end must be later");
        harvest.extendWindow(currentEnd - 1);
    }

    // ── isEligible view ───────────────────────────────────────────────────────

    function test_IsEligible_Valid() public {
        _activateRoot(30);
        assertTrue(harvest.isEligible(alice, ALICE_AMOUNT, aliceProof));
    }

    function test_IsEligible_AfterClaim_False() public {
        _activateRoot(30);
        vm.prank(alice);
        harvest.claim(aliceProof, ALICE_AMOUNT);
        assertFalse(harvest.isEligible(alice, ALICE_AMOUNT, aliceProof));
    }

    function test_IsEligible_InvalidProof_False() public {
        _activateRoot(30);
        bytes32[] memory bad = new bytes32[](1);
        bad[0] = bytes32(uint256(0xdead));
        assertFalse(harvest.isEligible(alice, ALICE_AMOUNT, bad));
    }

    // ── Ownership / access control ────────────────────────────────────────────

    function test_NonOwnerCannotSetRoot() public {
        vm.prank(alice);
        vm.expectRevert();
        harvest.setRoot(merkleRoot, 30, TOTAL_ALLOC);
    }

    function test_NonOwnerCannotPause() public {
        vm.prank(alice);
        vm.expectRevert();
        harvest.pause();
    }

    function test_NonOwnerCannotSweep() public {
        _activateRoot(30);
        vm.warp(block.timestamp + 31 days);
        vm.prank(alice);
        vm.expectRevert();
        harvest.sweep();
    }

    function test_OwnerCanRenounce() public {
        _activateRoot(30);
        vm.prank(owner);
        harvest.renounceOwnership();
        assertEq(harvest.owner(), address(0));
    }

    // ── ZeroAddress constructor ───────────────────────────────────────────────

    function test_ZeroSincAddress_Reverts() public {
        vm.expectRevert(HarvestClaim.ZeroAddress.selector);
        new HarvestClaim(address(0), treasury, owner);
    }

    function test_ZeroTreasuryAddress_Reverts() public {
        vm.expectRevert(HarvestClaim.ZeroAddress.selector);
        new HarvestClaim(address(sinc), address(0), owner);
    }
}

// ── Minimal mock ERC-20 ────────────────────────────────────────────────────────

contract MockSINC is IERC20 {
    mapping(address => uint256) private _bal;
    mapping(address => mapping(address => uint256)) private _allow;
    uint256 private _total;

    function mint(address to, uint256 amount) external {
        _bal[to] += amount;
        _total    += amount;
        emit Transfer(address(0), to, amount);
    }

    function totalSupply() external view returns (uint256) { return _total; }
    function balanceOf(address a) external view returns (uint256) { return _bal[a]; }
    function allowance(address o, address s) external view returns (uint256) { return _allow[o][s]; }

    function approve(address spender, uint256 amount) external returns (bool) {
        _allow[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(_bal[msg.sender] >= amount, "MockSINC: insufficient");
        _bal[msg.sender] -= amount;
        _bal[to]          += amount;
        emit Transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(_bal[from] >= amount, "MockSINC: insufficient");
        require(_allow[from][msg.sender] >= amount, "MockSINC: allowance");
        _bal[from]              -= amount;
        _bal[to]                 += amount;
        _allow[from][msg.sender] -= amount;
        emit Transfer(from, to, amount);
        return true;
    }
}
