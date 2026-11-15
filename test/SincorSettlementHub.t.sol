// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {SincorSettlementHub} from "../contracts/modules/SincorSettlementHub.sol";
import {MockUSDC} from "./mocks/MockUSDC.sol";

/// @notice Unit + negative tests for the production SincorSettlementHub.
///         SEALs are signed in-test with vm.sign; the digest is computed
///         INDEPENDENTLY from hardcoded literals (domain name "SINCOR_SEAL",
///         version "1", and the full SealAuthorization type string), never
///         from hub.DOMAIN_SEPARATOR() / hub.SEAL_TYPEHASH() at signing
///         time. This pins the EIP-712 identity: if the contract's domain
///         or type string changes, the independently computed values will
///         not match the contract's and these tests must fail.
///         Check order pinned by the sincor-x402 Python suite:
///         deadline -> sigil zero -> nonce -> signature.
contract SincorSettlementHubTest is Test {
    SincorSettlementHub public hub;
    MockUSDC public usdc;

    uint256 internal buyerKey = 0xB17E2;
    uint256 internal buyer2Key = 0xB17E3;
    uint256 internal attackerKey = 0xA77AC;

    address public buyer;
    address public buyer2;
    address public attacker;
    address public provider;
    address public treasury = address(0x7EA5);
    address public relayer;

    bytes32 public taskId = keccak256("hub-unit-task-1");
    bytes32 public schemaHash = keccak256("expected-schema");
    bytes32 public sigil = keccak256("sigil-commitment-1");

    event SealSettled(
        bytes32 indexed taskId,
        address indexed buyer,
        address indexed provider,
        uint256 netPayout,
        uint256 protocolFee,
        bytes32 sigilCommitment
    );

    function setUp() public {
        provider = makeAddr("provider");
        relayer = makeAddr("relayer");
        buyer = vm.addr(buyerKey);
        buyer2 = vm.addr(buyer2Key);
        attacker = vm.addr(attackerKey);

        usdc = new MockUSDC();
        hub = new SincorSettlementHub(address(usdc), treasury);

        usdc.mint(buyer, 10_000_000);
        usdc.mint(buyer2, 10_000_000);
        vm.prank(buyer);
        usdc.approve(address(hub), type(uint256).max);
        vm.prank(buyer2);
        usdc.approve(address(hub), type(uint256).max);
    }

    // ---- helpers -----------------------------------------------------------

    function _seal(address _buyer, address _provider, uint256 amount, uint256 nonce)
        internal
        view
        returns (SincorSettlementHub.SealAuthorization memory)
    {
        return SincorSettlementHub.SealAuthorization({
            taskId: taskId,
            buyer: _buyer,
            provider: _provider,
            amount: amount,
            expectedSchemaHash: schemaHash,
            deadline: block.timestamp + 1 hours,
            nonce: nonce
        });
    }

    // ---- independent EIP-712 identity (hardcoded literals) ------------------
    // These helpers compute the typehash and domain separator from literals
    // ONLY, so a change to the contract's strings breaks signing here.

    function _expectedSealTypehash() internal pure returns (bytes32) {
        return keccak256(
            bytes(
                "SealAuthorization(bytes32 taskId,address buyer,address provider,uint256 amount,bytes32 expectedSchemaHash,uint256 deadline,uint256 nonce)"
            )
        );
    }

    function _expectedDomainSeparator() internal view returns (bytes32) {
        return keccak256(
            abi.encode(
                keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
                keccak256(bytes("SINCOR_SEAL")),
                keccak256(bytes("1")),
                block.chainid,
                address(hub)
            )
        );
    }

    function _digest(SincorSettlementHub.SealAuthorization memory seal) internal view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                _expectedSealTypehash(),
                seal.taskId,
                seal.buyer,
                seal.provider,
                seal.amount,
                seal.expectedSchemaHash,
                seal.deadline,
                seal.nonce
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", _expectedDomainSeparator(), structHash));
    }

    function _sign(SincorSettlementHub.SealAuthorization memory seal, uint256 key)
        internal
        view
        returns (uint8 v, bytes32 r, bytes32 s)
    {
        return vm.sign(key, _digest(seal));
    }

    function _settle(SincorSettlementHub.SealAuthorization memory seal, uint256 key) internal returns (bool) {
        return _settleWithSigil(seal, key, sigil);
    }

    function _settleWithSigil(
        SincorSettlementHub.SealAuthorization memory seal,
        uint256 key,
        bytes32 sigilCommitment
    ) internal returns (bool) {
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, key);
        vm.prank(relayer);
        return hub.verifyAndSettleSeal(seal, sigilCommitment, v, r, s);
    }

    // ---- surface / config ----------------------------------------------------

    function test_ConfigMatchesCanon() public view {
        assertEq(address(hub.usdc()), address(usdc));
        assertEq(hub.feeTreasury(), treasury);
        assertEq(hub.protocolFeeBps(), 500);
        assertEq(hub.nonces(buyer), 0);
    }

    /// @notice Pins the EIP-712 identity independently: the expected
    ///         typehash and domain separator are computed in-test from
    ///         hardcoded literals and must match the contract's values.
    function test_EIP712Identity_PinnedToLiterals() public view {
        assertEq(hub.SEAL_TYPEHASH(), _expectedSealTypehash());
        assertEq(hub.DOMAIN_SEPARATOR(), _expectedDomainSeparator());
    }

    // ---- sigil overwrite (last-writer-wins, pinned) ---------------------------

    function test_SigilOverwrite_LastWriterWins_Pinned() public {
        bytes32 sigilA = keccak256("sigil-A");
        bytes32 sigilB = keccak256("sigil-B");

        assertTrue(_settleWithSigil(_seal(buyer, provider, 10_000, 0), buyerKey, sigilA));
        assertTrue(hub.isTaskVerified(taskId, sigilA));

        assertTrue(_settleWithSigil(_seal(buyer, provider, 10_000, 1), buyerKey, sigilB));
        assertTrue(hub.isTaskVerified(taskId, sigilB));
        assertFalse(hub.isTaskVerified(taskId, sigilA));
    }

    // ---- happy path ----------------------------------------------------------

    function test_HappyPath_ExactSplitEventNonceAndVerified() public {
        SincorSettlementHub.SealAuthorization memory seal = _seal(buyer, provider, 10_000, 0);
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, buyerKey);

        uint256 buyerBefore = usdc.balanceOf(buyer);

        vm.expectEmit(true, true, true, true, address(hub));
        emit SealSettled(taskId, buyer, provider, 9_500, 500, sigil);

        vm.prank(relayer);
        bool ok = hub.verifyAndSettleSeal(seal, sigil, v, r, s);

        assertTrue(ok);
        assertEq(usdc.balanceOf(provider), 9_500);
        assertEq(usdc.balanceOf(treasury), 500);
        assertEq(usdc.balanceOf(buyer), buyerBefore - 10_000);
        assertEq(hub.nonces(buyer), 1);
        assertTrue(hub.isTaskVerified(taskId, sigil));
        assertFalse(hub.isTaskVerified(taskId, keccak256("wrong-sigil")));
        assertFalse(hub.isTaskVerified(keccak256("other-task"), sigil));
    }

    // ---- fee-math edges --------------------------------------------------------

    function test_FeeEdge_Amount1() public {
        assertTrue(_settle(_seal(buyer, provider, 1, 0), buyerKey));
        assertEq(usdc.balanceOf(provider), 1);
        assertEq(usdc.balanceOf(treasury), 0);
    }

    function test_FeeEdge_Amount199() public {
        assertTrue(_settle(_seal(buyer, provider, 199, 0), buyerKey));
        assertEq(usdc.balanceOf(provider), 190);
        assertEq(usdc.balanceOf(treasury), 9);
    }

    function test_FeeEdge_Amount1000000() public {
        assertTrue(_settle(_seal(buyer, provider, 1_000_000, 0), buyerKey));
        assertEq(usdc.balanceOf(provider), 950_000);
        assertEq(usdc.balanceOf(treasury), 50_000);
    }

    /// @notice Pins the fixture's actual amount==0 behaviour (documented, not
    ///         changed): the seal settles successfully — fee 0, payout 0, no
    ///         value moves (a 0-value transferFrom succeeds), the sigil IS
    ///         recorded, and the nonce IS consumed.
    function test_FeeEdge_Amount0_PinnedFixtureBehaviour() public {
        uint256 buyerBefore = usdc.balanceOf(buyer);
        vm.expectEmit(true, true, true, true, address(hub));
        emit SealSettled(taskId, buyer, provider, 0, 0, sigil);
        assertTrue(_settle(_seal(buyer, provider, 0, 0), buyerKey));
        assertEq(usdc.balanceOf(buyer), buyerBefore);
        assertEq(usdc.balanceOf(provider), 0);
        assertEq(usdc.balanceOf(treasury), 0);
        assertEq(hub.nonces(buyer), 1);
        assertTrue(hub.isTaskVerified(taskId, sigil));
    }

    // ---- signature negatives ---------------------------------------------------

    function test_WrongSigner_RevertsInvalidSealSignature() public {
        SincorSettlementHub.SealAuthorization memory seal = _seal(buyer, provider, 10_000, 0);
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, attackerKey);
        uint256 buyerBefore = usdc.balanceOf(buyer);
        vm.expectRevert(SincorSettlementHub.InvalidSealSignature.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(seal, sigil, v, r, s);
        assertEq(usdc.balanceOf(buyer), buyerBefore);
        assertEq(usdc.balanceOf(provider), 0);
        assertEq(hub.nonces(buyer), 0);
        assertFalse(hub.isTaskVerified(taskId, sigil));
    }

    function test_TamperedAmount_RevertsInvalidSealSignature() public {
        SincorSettlementHub.SealAuthorization memory signed = _seal(buyer, provider, 10_000, 0);
        (uint8 v, bytes32 r, bytes32 s) = _sign(signed, buyerKey);
        SincorSettlementHub.SealAuthorization memory tampered = _seal(buyer, provider, 20_000, 0);
        vm.expectRevert(SincorSettlementHub.InvalidSealSignature.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(tampered, sigil, v, r, s);
        assertEq(hub.nonces(buyer), 0);
    }

    function test_TamperedProvider_RevertsInvalidSealSignature() public {
        SincorSettlementHub.SealAuthorization memory signed = _seal(buyer, provider, 10_000, 0);
        (uint8 v, bytes32 r, bytes32 s) = _sign(signed, buyerKey);
        SincorSettlementHub.SealAuthorization memory tampered = _seal(buyer, attacker, 10_000, 0);
        vm.expectRevert(SincorSettlementHub.InvalidSealSignature.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(tampered, sigil, v, r, s);
        assertEq(usdc.balanceOf(attacker), 0);
        assertEq(hub.nonces(buyer), 0);
    }

    // ---- deadline / nonce / sigil order -----------------------------------------

    function test_ExpiredDeadline_RevertsSealExpired() public {
        SincorSettlementHub.SealAuthorization memory seal = _seal(buyer, provider, 10_000, 0);
        seal.deadline = block.timestamp - 1;
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, buyerKey);
        vm.expectRevert(SincorSettlementHub.SealExpired.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(seal, sigil, v, r, s);
        assertEq(hub.nonces(buyer), 0);
    }

    function test_Replay_RevertsInvalidNonce() public {
        SincorSettlementHub.SealAuthorization memory seal = _seal(buyer, provider, 10_000, 0);
        assertTrue(_settle(seal, buyerKey));
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, buyerKey);
        vm.expectRevert(SincorSettlementHub.InvalidNonce.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(seal, sigil, v, r, s);
        // Provider was paid exactly once.
        assertEq(usdc.balanceOf(provider), 9_500);
        assertEq(hub.nonces(buyer), 1);
    }

    function test_FutureNonce_RevertsInvalidNonce() public {
        SincorSettlementHub.SealAuthorization memory seal = _seal(buyer, provider, 10_000, 1);
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, buyerKey);
        vm.expectRevert(SincorSettlementHub.InvalidNonce.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(seal, sigil, v, r, s);
        assertEq(hub.nonces(buyer), 0);
    }

    function test_ZeroSigil_RevertsSigilCommitmentZero() public {
        SincorSettlementHub.SealAuthorization memory seal = _seal(buyer, provider, 10_000, 0);
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, buyerKey);
        vm.expectRevert(SincorSettlementHub.SigilCommitmentZero.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(seal, bytes32(0), v, r, s);
        assertEq(hub.nonces(buyer), 0);
    }

    /// @notice Check-order pin: expired + zero sigil -> SealExpired wins
    ///         (deadline is checked before the sigil).
    function test_CheckOrder_ExpiredBeatsZeroSigil() public {
        SincorSettlementHub.SealAuthorization memory seal = _seal(buyer, provider, 10_000, 0);
        seal.deadline = block.timestamp - 1;
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, buyerKey);
        vm.expectRevert(SincorSettlementHub.SealExpired.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(seal, bytes32(0), v, r, s);
    }

    /// @notice Check-order pin: zero sigil + bad nonce -> SigilCommitmentZero
    ///         wins (sigil is checked before the nonce).
    function test_CheckOrder_ZeroSigilBeatsBadNonce() public {
        SincorSettlementHub.SealAuthorization memory seal = _seal(buyer, provider, 10_000, 7);
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, buyerKey);
        vm.expectRevert(SincorSettlementHub.SigilCommitmentZero.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(seal, bytes32(0), v, r, s);
    }

    // ---- transfer failures --------------------------------------------------------

    function test_InsufficientAllowance_RevertsUsdcTransferFailed() public {
        uint256 poorKey = 0x9002;
        address poor = vm.addr(poorKey);
        usdc.mint(poor, 10_000_000);
        vm.prank(poor);
        usdc.approve(address(hub), 100); // far below the seal amount

        SincorSettlementHub.SealAuthorization memory seal = _seal(poor, provider, 10_000, 0);
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, poorKey);
        vm.expectRevert(SincorSettlementHub.UsdcTransferFailed.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(seal, sigil, v, r, s);
        assertEq(usdc.balanceOf(provider), 0);
        assertEq(hub.nonces(poor), 0);
    }

    function test_InsufficientBalance_RevertsUsdcTransferFailed() public {
        uint256 brokeKey = 0xB201;
        address broke = vm.addr(brokeKey);
        usdc.mint(broke, 100); // balance far below the seal amount
        vm.prank(broke);
        usdc.approve(address(hub), type(uint256).max);

        SincorSettlementHub.SealAuthorization memory seal = _seal(broke, provider, 10_000, 0);
        (uint8 v, bytes32 r, bytes32 s) = _sign(seal, brokeKey);
        vm.expectRevert(SincorSettlementHub.UsdcTransferFailed.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(seal, sigil, v, r, s);
        assertEq(usdc.balanceOf(broke), 100);
        assertEq(hub.nonces(broke), 0);
    }

    // ---- multi-buyer nonce independence --------------------------------------------

    function test_TwoBuyers_NoncesIndependent() public {
        assertTrue(_settle(_seal(buyer, provider, 10_000, 0), buyerKey));
        assertTrue(_settle(_seal(buyer2, provider, 10_000, 0), buyer2Key));
        assertEq(hub.nonces(buyer), 1);
        assertEq(hub.nonces(buyer2), 1);
        // Buyer 1's next seal uses nonce 1; buyer 2 replaying nonce 0 fails
        // on its own track and does not disturb buyer 1.
        assertTrue(_settle(_seal(buyer, provider, 10_000, 1), buyerKey));
        SincorSettlementHub.SealAuthorization memory replay = _seal(buyer2, provider, 10_000, 0);
        (uint8 v, bytes32 r, bytes32 s) = _sign(replay, buyer2Key);
        vm.expectRevert(SincorSettlementHub.InvalidNonce.selector);
        vm.prank(relayer);
        hub.verifyAndSettleSeal(replay, sigil, v, r, s);
        assertEq(hub.nonces(buyer), 2);
        assertEq(hub.nonces(buyer2), 1);
    }

    // ---- fuzz -----------------------------------------------------------------------

    function testFuzz_FeeSplitConservation(uint256 amount) public {
        amount = bound(amount, 0, 10_000_000);
        SincorSettlementHub.SealAuthorization memory seal = _seal(buyer, provider, amount, 0);
        assertTrue(_settle(seal, buyerKey));
        uint256 fee = usdc.balanceOf(treasury);
        uint256 payout = usdc.balanceOf(provider);
        assertEq(fee, (amount * 500) / 10_000);
        assertEq(payout + fee, amount);
        assertEq(hub.nonces(buyer), 1);
    }
}
