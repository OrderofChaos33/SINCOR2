// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SigilPolicyModule} from "../contracts/modules/SigilPolicyModule.sol";
import {SincorSettlementHub} from "../contracts/modules/SincorSettlementHub.sol";
import {ISincorSettlementHub} from "../contracts/modules/SigilPolicyModule.sol";

contract MockToken is ERC20 {
    constructor() ERC20("Mock", "MCK") {}
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

/// @notice Minimal ERC-7579-style account for the full-loop test: installs the
///         hook, and — per EIP-7579's hook extension — calls preCheck before
///         and postCheck after every execute(). The "session key" is a plain
///         address allowed to trigger execute(), standing in for a Kernel
///         permission/signer setup; the hook is the gate under test.
contract MockERC7579Account {
    SigilPolicyModule public hook;
    address public sessionKey;

    constructor(SigilPolicyModule hook_, address sessionKey_) {
        hook = hook_;
        sessionKey = sessionKey_;
    }

    function installHook(bytes calldata initData) external {
        hook.onInstall(initData);
    }

    function uninstallHook() external {
        hook.onUninstall(bytes(""));
    }

    receive() external payable {}

    /// @dev executionCalldata is the ERC-7579 packed single call:
    ///      abi.encodePacked(target, value, innerCallData).
    function execute(bytes calldata executionCalldata) external returns (bytes memory) {
        require(msg.sender == sessionKey, "not session key");
        bytes memory hookData = hook.preCheck(msg.sender, 0, executionCalldata);
        address target = address(bytes20(executionCalldata[0:20]));
        uint256 value = uint256(bytes32(executionCalldata[20:52]));
        bytes calldata inner = executionCalldata[52:];
        (bool ok, bytes memory ret) = target.call{value: value}(inner);
        require(ok, "execution failed");
        hook.postCheck(hookData);
        return ret;
    }
}

contract SigilPolicyModuleTest is Test {
    SigilPolicyModule public module;
    MockERC7579Account public account;
    MockToken public token;

    uint256 public verifierPk = 0xA11CE5;
    address public verifier;
    uint256 public attackerPk = 0xBAD;
    address public sessionKey = address(0x5E5510);
    address public payee = address(0x9A11);
    address public payee2 = address(0x9A22);

    uint256 public constant POLICY_CAP = 10 ether;
    uint256 public nonceCounter;

    function setUp() public {
        verifier = vm.addr(verifierPk);
        module = new SigilPolicyModule();
        account = new MockERC7579Account(module, sessionKey);
        token = new MockToken();
        vm.deal(address(account), 100 ether);
        token.mint(address(account), 100 ether);

        vm.prank(address(account));
        account.installHook(abi.encode(verifier, POLICY_CAP, uint256(0), address(0)));
    }

    // ------------------------------------------------------------- helpers

    function _att(address token_, address payee_, uint256 cap, uint64 expiry)
        internal
        returns (SigilPolicyModule.SigilAttestation memory att)
    {
        att = SigilPolicyModule.SigilAttestation({
            account: address(account),
            taskSpecHash: keccak256("task-spec-1"),
            resultCommitment: keccak256("result-1"),
            payee: payee_,
            token: token_,
            amountCap: cap,
            expiry: expiry,
            nonce: ++nonceCounter
        });
    }

    function _sign(SigilPolicyModule.SigilAttestation memory att, uint256 pk) internal view returns (bytes memory) {
        bytes32 digest = module.attestationDigest(att);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        return abi.encodePacked(r, s, v);
    }

    function _registerValid(address token_, address payee_, uint256 cap)
        internal
        returns (SigilPolicyModule.SigilAttestation memory att)
    {
        att = _att(token_, payee_, cap, uint64(block.timestamp + 1 hours));
        module.registerAttestation(att, _sign(att, verifierPk));
    }

    function _packedNative(address to, uint256 amount) internal pure returns (bytes memory) {
        return abi.encodePacked(to, amount, bytes(""));
    }

    function _packedERC20(MockToken token_, address to, uint256 amount) internal pure returns (bytes memory) {
        return abi.encodePacked(
            address(token_), uint256(0), abi.encodeWithSelector(IERC20.transfer.selector, to, amount)
        );
    }

    // ------------------------------------------------------- module basics

    function test_IsModuleTypeHookOnly() public view {
        assertTrue(module.isModuleType(4));
        assertFalse(module.isModuleType(1));
        assertFalse(module.isModuleType(5));
    }

    function test_OnInstallStoresConfig() public view {
        (address v, uint256 cap, uint256 threshold, address hub, bool init) = module.configOf(address(account));
        assertEq(v, verifier);
        assertEq(cap, POLICY_CAP);
        assertEq(threshold, 0);
        assertEq(hub, address(0));
        assertTrue(init);
        assertTrue(module.isInitialized(address(account)));
    }

    function test_OnInstallTwiceReverts() public {
        vm.prank(address(account));
        vm.expectRevert(abi.encodeWithSelector(SigilPolicyModule.AlreadyInstalled.selector, address(account)));
        account.installHook(abi.encode(verifier, POLICY_CAP, uint256(0), address(0)));
    }

    function test_OnInstallZeroVerifierReverts() public {
        MockERC7579Account other = new MockERC7579Account(module, sessionKey);
        vm.prank(address(other));
        vm.expectRevert(SigilPolicyModule.InvalidVerifier.selector);
        other.installHook(abi.encode(address(0), POLICY_CAP, uint256(0), address(0)));
    }

    function test_RegisterOnUninstalledAccountReverts() public {
        SigilPolicyModule.SigilAttestation memory att = _att(address(0), payee, 1 ether, uint64(block.timestamp + 1 hours));
        att.account = address(0xDEAD);
        bytes memory sig = _sign(att, verifierPk);
        vm.expectRevert(abi.encodeWithSelector(SigilPolicyModule.NotInstalled.selector, address(0xDEAD)));
        module.registerAttestation(att, sig);
    }

    // ------------------------------------------------- attestation checks

    function test_ValidAttestationRegisters() public {
        SigilPolicyModule.SigilAttestation memory att = _registerValid(address(0), payee, 2 ether);
        (bytes32 digest,,,,,, bool exists) = module.pendingOf(address(account), address(0), payee);
        assertTrue(exists);
        assertEq(digest, module.attestationDigest(att));
    }

    function test_WrongSignerReverts() public {
        SigilPolicyModule.SigilAttestation memory att =
            _att(address(0), payee, 1 ether, uint64(block.timestamp + 1 hours));
        bytes memory badSig = _sign(att, attackerPk);
        vm.expectRevert();
        module.registerAttestation(att, badSig);
    }

    function test_ExpiredAttestationRegisterReverts() public {
        vm.warp(1000);
        SigilPolicyModule.SigilAttestation memory att = _att(address(0), payee, 1 ether, 999);
        bytes memory sig = _sign(att, verifierPk);
        vm.expectRevert();
        module.registerAttestation(att, sig);
    }

    function test_OverPolicyCapRegisterReverts() public {
        SigilPolicyModule.SigilAttestation memory att =
            _att(address(0), payee, POLICY_CAP + 1, uint64(block.timestamp + 1 hours));
        bytes memory sig = _sign(att, verifierPk);
        vm.expectRevert(
            abi.encodeWithSelector(SigilPolicyModule.OverPolicyCap.selector, POLICY_CAP + 1, POLICY_CAP)
        );
        module.registerAttestation(att, sig);
    }

    function test_SameAttestationRegisteredTwiceReverts() public {
        SigilPolicyModule.SigilAttestation memory att =
            _att(address(0), payee, 1 ether, uint64(block.timestamp + 1 hours));
        bytes memory sig = _sign(att, verifierPk);
        module.registerAttestation(att, sig);
        vm.expectRevert();
        module.registerAttestation(att, sig);
    }

    function test_NonceReuseWithDifferentAttestationReverts() public {
        SigilPolicyModule.SigilAttestation memory att1 = _registerValid(address(0), payee, 1 ether);
        SigilPolicyModule.SigilAttestation memory att2 =
            _att(address(0), payee2, 1 ether, uint64(block.timestamp + 1 hours));
        att2.nonce = att1.nonce; // reuse the nonce, different digest
        bytes memory sig2 = _sign(att2, verifierPk);
        vm.expectRevert(
            abi.encodeWithSelector(SigilPolicyModule.NonceReplayed.selector, address(account), att1.nonce)
        );
        module.registerAttestation(att2, sig2);
    }

    function test_AttestationBoundToOtherAccountCannotRegisterHere() public {
        // Verifier signs for a different account; registering it against this
        // account's config is impossible because the digest binds `account`,
        // and this test presents it with account = other, which is not installed.
        SigilPolicyModule.SigilAttestation memory att =
            _att(address(0), payee, 1 ether, uint64(block.timestamp + 1 hours));
        att.account = address(0xBEEF);
        bytes memory sig = _sign(att, verifierPk);
        vm.expectRevert(abi.encodeWithSelector(SigilPolicyModule.NotInstalled.selector, address(0xBEEF)));
        module.registerAttestation(att, sig);
    }

    // --------------------------------------------------------- full loop

    function test_PaymentWithoutAttestationBlocked() public {
        vm.prank(sessionKey);
        vm.expectRevert(
            abi.encodeWithSelector(SigilPolicyModule.NoValidAttestation.selector, address(account), address(0), payee)
        );
        account.execute(_packedNative(payee, 1 ether));
        assertEq(payee.balance, 0);
    }

    function test_FullLoop_BlockThenExecuteThenReplayBlocked() public {
        bytes memory exec = _packedNative(payee, 1 ether);

        // 1. No attestation -> blocked.
        vm.prank(sessionKey);
        vm.expectRevert();
        account.execute(exec);
        assertEq(payee.balance, 0);

        // 2. Valid attestation attached (registered) -> executes.
        _registerValid(address(0), payee, 2 ether);
        vm.prank(sessionKey);
        account.execute(exec);
        assertEq(payee.balance, 1 ether);

        // 3. Replay the same execution -> attestation was consumed -> blocked.
        vm.prank(sessionKey);
        vm.expectRevert();
        account.execute(exec);
        assertEq(payee.balance, 1 ether);
    }

    function test_ConsumedAttestationCannotBeReRegistered() public {
        SigilPolicyModule.SigilAttestation memory att =
            _att(address(0), payee, 2 ether, uint64(block.timestamp + 1 hours));
        bytes memory sig = _sign(att, verifierPk);
        module.registerAttestation(att, sig);

        vm.prank(sessionKey);
        account.execute(_packedNative(payee, 1 ether));

        vm.expectRevert(abi.encodeWithSelector(SigilPolicyModule.AttestationReplayed.selector, module.attestationDigest(att)));
        module.registerAttestation(att, sig);
    }

    function test_OverAttestationCapPaymentBlocked() public {
        _registerValid(address(0), payee, 1 ether); // cap 1 ETH
        vm.prank(sessionKey);
        vm.expectRevert(abi.encodeWithSelector(SigilPolicyModule.OverAttestationCap.selector, 2 ether, 1 ether));
        account.execute(_packedNative(payee, 2 ether)); // tries 2 ETH
        assertEq(payee.balance, 0);
    }

    function test_AttestationExpiresBeforeExecutionBlocks() public {
        SigilPolicyModule.SigilAttestation memory att =
            _att(address(0), payee, 2 ether, uint64(block.timestamp + 100));
        module.registerAttestation(att, _sign(att, verifierPk));

        vm.warp(block.timestamp + 101);
        vm.prank(sessionKey);
        vm.expectRevert();
        account.execute(_packedNative(payee, 1 ether));
        assertEq(payee.balance, 0);
    }

    function test_PayeeMismatchBlocked() public {
        _registerValid(address(0), payee, 2 ether); // attestation is for `payee`
        vm.prank(sessionKey);
        vm.expectRevert();
        account.execute(_packedNative(payee2, 1 ether)); // payment to someone else
        assertEq(payee2.balance, 0);
    }

    function test_ERC20TransferGatedAndExecutes() public {
        bytes memory exec = _packedERC20(token, payee, 3 ether);

        vm.prank(sessionKey);
        vm.expectRevert();
        account.execute(exec);
        assertEq(token.balanceOf(payee), 0);

        _registerValid(address(token), payee, 5 ether);
        vm.prank(sessionKey);
        account.execute(exec);
        assertEq(token.balanceOf(payee), 3 ether);
    }

    function test_NonPaymentCallAllowedWithoutAttestation() public {
        // A zero-value call with no transfer semantics is not a fund release.
        bytes memory exec = abi.encodePacked(payee2, uint256(0), bytes(""));
        vm.prank(sessionKey);
        account.execute(exec); // must not revert
    }

    function test_UninstallClearsConfigAndBlocksRegistration() public {
        vm.prank(address(account));
        account.uninstallHook();
        assertFalse(module.isInitialized(address(account)));

        SigilPolicyModule.SigilAttestation memory att =
            _att(address(0), payee, 1 ether, uint64(block.timestamp + 1 hours));
        bytes memory sig = _sign(att, verifierPk);
        vm.expectRevert(abi.encodeWithSelector(SigilPolicyModule.NotInstalled.selector, address(account)));
        module.registerAttestation(att, sig);
    }

    function test_AccountCanRevokePendingAttestation() public {
        _registerValid(address(0), payee, 2 ether);
        vm.prank(address(account));
        module.revokeAttestation(address(0), payee);

        vm.prank(sessionKey);
        vm.expectRevert();
        account.execute(_packedNative(payee, 1 ether));
    }
}

contract MockUSDC is ERC20 {
    constructor() ERC20("USD Coin (mock)", "USDC") {}
    function decimals() public pure override returns (uint8) {
        return 6;
    }
    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

/// @notice Spec-drop correction tests (2026-11-11):
///  (a) the unverified threshold is applied to the amount DECODED from the
///      execution calldata — a USDC transfer carries msgValue == 0, and the
///      draft hook's msgValue comparison would never gate it;
///  (b) the Settlement Hub integration calls ONLY the Hub's real
///      isTaskVerified(bytes32,bytes32), and the Hub deploys at the locked
///      canon fee of 500 bps (draft said 100).
contract SigilThresholdAndHubTest is Test {
    SigilPolicyModule public module;
    SincorSettlementHub public hub;
    MockUSDC public usdc;

    uint256 public verifierPk = 0xA11CE5;
    address public verifier;
    address public sessionKey = address(0x5E5510);
    address public payee = address(0x9A11);
    address public treasury = address(0x7EA5);

    uint256 public constant THRESHOLD = 5_000_000; // 5 USDC (6-dec raw units)
    uint256 public nonceCounter;

    function setUp() public {
        verifier = vm.addr(verifierPk);
        module = new SigilPolicyModule();
        usdc = new MockUSDC();
        hub = new SincorSettlementHub(address(usdc), treasury);
    }

    function _newAccount(uint256 threshold, address hub_) internal returns (MockERC7579Account acct) {
        acct = new MockERC7579Account(module, sessionKey);
        usdc.mint(address(acct), 1_000_000_000); // 1,000 USDC
        vm.deal(address(acct), 10 ether);
        vm.prank(address(acct));
        acct.installHook(abi.encode(verifier, uint256(10_000_000_000), threshold, hub_));
    }

    function _packedUSDC(MockERC7579Account, address to, uint256 amount) internal view returns (bytes memory) {
        return abi.encodePacked(
            address(usdc), uint256(0), abi.encodeWithSelector(IERC20.transfer.selector, to, amount)
        );
    }

    function _attFor(MockERC7579Account acct, bytes32 taskId, bytes32 commitment, uint256 cap)
        internal
        returns (SigilPolicyModule.SigilAttestation memory att)
    {
        att = SigilPolicyModule.SigilAttestation({
            account: address(acct),
            taskSpecHash: taskId,
            resultCommitment: commitment,
            payee: payee,
            token: address(usdc),
            amountCap: cap,
            expiry: uint64(block.timestamp + 1 hours),
            nonce: ++nonceCounter
        });
        bytes32 digest = module.attestationDigest(att);
        (uint8 v, bytes32 r, bytes32 s_) = vm.sign(verifierPk, digest);
        module.registerAttestation(att, abi.encodePacked(r, s_, v));
    }

    function _settleOnHub(bytes32 taskId, bytes32 commitment, uint256 buyerPk, uint256 amount)
        internal
        returns (address buyer)
    {
        buyer = vm.addr(buyerPk);
        usdc.mint(buyer, amount);
        SincorSettlementHub.SealAuthorization memory seal = SincorSettlementHub.SealAuthorization({
            taskId: taskId,
            buyer: buyer,
            provider: payee,
            amount: amount,
            expectedSchemaHash: keccak256("schema-v1"),
            deadline: block.timestamp + 1 hours,
            nonce: 0
        });
        bytes32 structHash = keccak256(
            abi.encode(hub.SEAL_TYPEHASH(), seal.taskId, seal.buyer, seal.provider, seal.amount, seal.expectedSchemaHash, seal.deadline, seal.nonce)
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", hub.DOMAIN_SEPARATOR(), structHash));
        (uint8 v, bytes32 r, bytes32 s_) = vm.sign(buyerPk, digest);
        vm.prank(buyer);
        usdc.approve(address(hub), amount);
        hub.verifyAndSettleSeal(seal, commitment, v, r, s_);
    }

    // ------------------------------------------------------ threshold fix

    /// THE spec-drop regression test: a 100 USDC transfer executed with
    /// msgValue == 0 (as every ERC-20 transfer is) must be BLOCKED above
    /// the unverified threshold when no attestation exists. The draft
    /// hook compared msgValue (0) <= threshold and waved it through.
    function test_USDCTransferAboveThresholdBlockedWithZeroMsgValue() public {
        MockERC7579Account acct = _newAccount(THRESHOLD, address(0));
        bytes memory exec = _packedUSDC(acct, payee, 100_000_000); // 100 USDC, packed value = 0
        vm.prank(sessionKey);
        vm.expectRevert(
            abi.encodeWithSelector(SigilPolicyModule.NoValidAttestation.selector, address(acct), address(usdc), payee)
        );
        acct.execute(exec);
        assertEq(usdc.balanceOf(payee), 0);
    }

    function test_USDCTransferAtThresholdAllowedWithoutAttestation() public {
        MockERC7579Account acct = _newAccount(THRESHOLD, address(0));
        vm.prank(sessionKey);
        acct.execute(_packedUSDC(acct, payee, THRESHOLD)); // exactly 5 USDC
        assertEq(usdc.balanceOf(payee), THRESHOLD);
    }

    function test_USDCTransferFromAboveThresholdBlockedWithZeroMsgValue() public {
        MockERC7579Account acct = _newAccount(THRESHOLD, address(0));
        bytes memory exec = abi.encodePacked(
            address(usdc),
            uint256(0),
            abi.encodeWithSelector(IERC20.transferFrom.selector, address(acct), payee, uint256(100_000_000))
        );
        vm.prank(sessionKey);
        vm.expectRevert();
        acct.execute(exec);
        assertEq(usdc.balanceOf(payee), 0);
    }

    function test_NativeBelowThresholdAllowedWithoutAttestation() public {
        MockERC7579Account acct = _newAccount(2 ether, address(0));
        vm.prank(sessionKey);
        acct.execute(abi.encodePacked(payee, uint256(1 ether), bytes("")));
        assertEq(payee.balance, 1 ether);
    }

    // ------------------------------------------------------------ hub gate

    function test_HubConfigured_ReleaseBlockedUntilCommitmentRecorded() public {
        MockERC7579Account acct = _newAccount(THRESHOLD, address(hub));
        bytes32 taskId = keccak256("task-42");
        bytes32 commitment = keccak256("sigil-42");
        _attFor(acct, taskId, commitment, 200_000_000);

        // Valid verifier attestation, but the Hub has NO recorded
        // commitment for this task -> blocked. (isTaskVerified proves a
        // commitment was recorded; it is not on-chain verification.)
        vm.prank(sessionKey);
        vm.expectRevert(abi.encodeWithSelector(SigilPolicyModule.CommitmentNotRecorded.selector, taskId));
        acct.execute(_packedUSDC(acct, payee, 100_000_000));
        assertEq(usdc.balanceOf(payee), 0);
    }

    function test_HubConfigured_ReleaseExecutesAfterCommitmentRecorded() public {
        MockERC7579Account acct = _newAccount(THRESHOLD, address(hub));
        bytes32 taskId = keccak256("task-42");
        bytes32 commitment = keccak256("sigil-42");
        _attFor(acct, taskId, commitment, 200_000_000);

        _settleOnHub(taskId, commitment, 0xB0B, 100_000_000);
        assertTrue(hub.isTaskVerified(taskId, commitment));
        uint256 payeeBefore = usdc.balanceOf(payee); // includes the hub payout

        vm.prank(sessionKey);
        acct.execute(_packedUSDC(acct, payee, 100_000_000));
        assertEq(usdc.balanceOf(payee), payeeBefore + 100_000_000);
    }

    // ------------------------------------------------------------ hub fees

    function test_HubFeeIsCanon500BpsAndSplits() public {
        assertEq(hub.protocolFeeBps(), 500);
        bytes32 taskId = keccak256("task-fee");
        _settleOnHub(taskId, keccak256("sigil-fee"), 0xB0B, 100_000_000); // 100 USDC
        assertEq(usdc.balanceOf(treasury), 5_000_000); // exactly 5%
        assertEq(usdc.balanceOf(payee), 95_000_000); // exactly 95%
    }

    function test_HubSettleWrongSignerReverts() public {
        bytes32 taskId = keccak256("task-bad");
        address buyer = vm.addr(0xB0B);
        usdc.mint(buyer, 100_000_000);
        SincorSettlementHub.SealAuthorization memory seal = SincorSettlementHub.SealAuthorization({
            taskId: taskId, buyer: buyer, provider: payee, amount: 100_000_000,
            expectedSchemaHash: keccak256("schema-v1"), deadline: block.timestamp + 1 hours, nonce: 0
        });
        bytes32 structHash = keccak256(
            abi.encode(hub.SEAL_TYPEHASH(), seal.taskId, seal.buyer, seal.provider, seal.amount, seal.expectedSchemaHash, seal.deadline, seal.nonce)
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", hub.DOMAIN_SEPARATOR(), structHash));
        (uint8 v, bytes32 r, bytes32 s_) = vm.sign(0xDEAD, digest); // not the buyer
        vm.prank(buyer);
        usdc.approve(address(hub), 100_000_000);
        vm.expectRevert(SincorSettlementHub.InvalidSealSignature.selector);
        hub.verifyAndSettleSeal(seal, keccak256("sigil-bad"), v, r, s_);
        assertFalse(hub.isTaskVerified(taskId, keccak256("sigil-bad")));
    }

    function test_HubSettleExpiredReverts() public {
        bytes32 taskId = keccak256("task-old");
        uint256 buyerPk = 0xB0B;
        address buyer = vm.addr(buyerPk);
        usdc.mint(buyer, 100_000_000);
        SincorSettlementHub.SealAuthorization memory seal = SincorSettlementHub.SealAuthorization({
            taskId: taskId, buyer: buyer, provider: payee, amount: 100_000_000,
            expectedSchemaHash: keccak256("schema-v1"), deadline: block.timestamp - 1, nonce: 0
        });
        bytes32 structHash = keccak256(
            abi.encode(hub.SEAL_TYPEHASH(), seal.taskId, seal.buyer, seal.provider, seal.amount, seal.expectedSchemaHash, seal.deadline, seal.nonce)
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", hub.DOMAIN_SEPARATOR(), structHash));
        (uint8 v, bytes32 r, bytes32 s_) = vm.sign(buyerPk, digest);
        vm.prank(buyer);
        usdc.approve(address(hub), 100_000_000);
        vm.expectRevert(SincorSettlementHub.SealExpired.selector);
        hub.verifyAndSettleSeal(seal, keccak256("sigil-old"), v, r, s_);
    }
}
