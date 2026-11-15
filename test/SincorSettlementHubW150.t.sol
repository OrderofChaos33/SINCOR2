// SPDX-License-Identifier: MIT
// W-150 REGRESSION — SincorSettlementHub sigil binding.
//
// Red-team hunt 47 (poc47_hub_sigil_frontrun.t.sol) proved against the
// pre-fix Hub: (1) a front-runner could settle a buyer-signed SEAL with a
// junk sigilCommitment of the submitter's choosing (the parameter was not
// covered by the buyer's EIP-712 signature), bricking the worker's
// SigilPolicyModule Hub gate until the buyer re-signed; (2) an amount-0
// seal settled and recorded "verified" with zero USDC moved, and the
// ERC-8004 oracle granted +10 reputation for it.
//
// Fix: `sigilCommitment` is now a field of the signed SealAuthorization
// struct (SEAL_TYPEHASH updated), verifyAndSettleSeal takes no separate
// commitment parameter, and amount == 0 reverts SealAmountZero.
// These tests pin the closure: the old attack shapes revert, and the
// honest path (buyer-signed genuine sigil -> settle -> module payment)
// works end-to-end with no buyer re-sign.
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SigilPolicyModule} from "../contracts/modules/SigilPolicyModule.sol";
import {SincorSettlementHub} from "../contracts/modules/SincorSettlementHub.sol";
import {SincorValidationReputationOracle} from "../contracts/erc8004/SincorValidationReputationOracle.sol";

contract W150MockToken is ERC20 {
    constructor() ERC20("MockUSDC", "MUSDC") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

contract W150MockAccount {
    SigilPolicyModule public hook;
    address public sessionKey;
    constructor(SigilPolicyModule hook_, address sessionKey_) { hook = hook_; sessionKey = sessionKey_; }
    function installHook(bytes calldata initData) external { hook.onInstall(initData); }
    receive() external payable {}
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

contract SincorSettlementHubW150Test is Test {
    SincorSettlementHub public hub;
    SigilPolicyModule public module;
    W150MockAccount public account;
    W150MockToken public usdc;
    SincorValidationReputationOracle public oracle;

    address public treasury = address(0x7EA5);
    address public payee = address(0x9A11);
    address public sessionKey = address(0x5E5510);
    address public attacker = address(0xA77A);
    uint256 public buyerPk = 0xB0B;
    address public buyer;
    uint256 public verifierPk = 0xA11CE5;

    bytes32 public constant TASK = keccak256("task-42");
    bytes32 public constant GENUINE = keccak256("sigil-genuine");
    bytes32 public constant JUNK = keccak256("sigil-junk-attacker-chosen");

    function setUp() public {
        buyer = vm.addr(buyerPk);
        usdc = new W150MockToken();
        hub = new SincorSettlementHub(address(usdc), treasury);
        module = new SigilPolicyModule();
        account = new W150MockAccount(module, sessionKey);
        oracle = new SincorValidationReputationOracle(address(hub)); // this test contract is owner
        usdc.mint(buyer, 1_000_000_000);
        usdc.mint(address(account), 200_000_000);
        vm.prank(buyer);
        usdc.approve(address(hub), type(uint256).max);
        vm.prank(address(account));
        account.installHook(
            abi.encode(vm.addr(verifierPk), 500_000_000, uint256(0), address(hub))
        );
    }

    function _signSeal(SincorSettlementHub.SealAuthorization memory seal, uint256 pk)
        internal
        view
        returns (uint8 v, bytes32 r, bytes32 s)
    {
        bytes32 structHash = keccak256(
            abi.encode(
                hub.SEAL_TYPEHASH(),
                seal.taskId, seal.buyer, seal.provider, seal.amount,
                seal.expectedSchemaHash, seal.sigilCommitment, seal.deadline, seal.nonce
            )
        );
        bytes32 digest = keccak256(abi.encodePacked("\x19\x01", hub.DOMAIN_SEPARATOR(), structHash));
        (v, r, s) = vm.sign(pk, digest);
    }

    function _seal(uint256 amount, uint256 nonce, bytes32 sigil)
        internal
        view
        returns (SincorSettlementHub.SealAuthorization memory)
    {
        return SincorSettlementHub.SealAuthorization({
            taskId: TASK,
            buyer: buyer,
            provider: payee,
            amount: amount,
            expectedSchemaHash: keccak256("schema-v1"),
            sigilCommitment: sigil,
            deadline: block.timestamp + 1 hours,
            nonce: nonce
        });
    }

    function _registerAttestation() internal {
        SigilPolicyModule.SigilAttestation memory att = SigilPolicyModule.SigilAttestation({
            account: address(account),
            taskSpecHash: TASK,
            resultCommitment: GENUINE,
            payee: payee,
            token: address(usdc),
            amountCap: 200_000_000,
            expiry: uint64(block.timestamp + 1 hours),
            nonce: 1
        });
        bytes32 digest = module.attestationDigest(att);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(verifierPk, digest);
        module.registerAttestation(att, abi.encodePacked(r, s, v));
    }

    function _modulePay(uint256 amount) internal {
        bytes memory callData = abi.encodePacked(
            address(usdc),
            uint256(0),
            abi.encodeWithSelector(IERC20.transfer.selector, payee, amount)
        );
        vm.prank(sessionKey);
        account.execute(callData);
    }

    /// @notice The PoC's front-run poison is closed two ways: there is no
    ///         submitter-chosen commitment parameter anymore, and swapping
    ///         the signed struct's sigil field invalidates the buyer's
    ///         signature. Relaying the buyer's seal UNMODIFIED records the
    ///         GENUINE commitment, and the module-gated payment flows with
    ///         no buyer re-sign.
    function test_FrontrunPoison_Closed_SigilIsSignatureBound() public {
        _registerAttestation();
        SincorSettlementHub.SealAuthorization memory seal = _seal(100_000_000, 0, GENUINE);
        (uint8 v, bytes32 r, bytes32 s) = _signSeal(seal, buyerPk);

        // ATTACK (old shape, adapted): the attacker rewrites the sigil
        // inside the seal to JUNK and replays the buyer's signature.
        SincorSettlementHub.SealAuthorization memory poisoned = _seal(100_000_000, 0, JUNK);
        vm.prank(attacker);
        vm.expectRevert(SincorSettlementHub.InvalidSealSignature.selector);
        hub.verifyAndSettleSeal(poisoned, v, r, s);
        assertFalse(hub.isTaskVerified(TASK, JUNK), "junk can never be recorded");
        assertEq(usdc.balanceOf(payee), 0, "no funds moved by the poison attempt");

        // HONEST RELAY: the attacker (or anyone) submits the buyer's seal
        // exactly as signed — the recorded commitment is the buyer's own.
        vm.prank(attacker);
        hub.verifyAndSettleSeal(seal, v, r, s);
        assertEq(usdc.balanceOf(payee), 95_000_000, "funds routed correctly");
        assertEq(usdc.balanceOf(treasury), 5_000_000, "5% fee taken");
        assertTrue(hub.isTaskVerified(TASK, GENUINE), "buyer-signed sigil recorded");
        assertFalse(hub.isTaskVerified(TASK, JUNK), "junk still not recorded");

        // The module-gated payment for the genuine attestation works
        // immediately — no buyer re-sign, no brick.
        _modulePay(100_000_000);
        assertEq(usdc.balanceOf(payee), 95_000_000 + 100_000_000, "module payment flows");
    }

    /// @notice The PoC's amount-0 seal is closed: it reverts on-chain, so
    ///         no "verified" record can exist for a zero payment, and the
    ///         oracle's verified path has nothing to accept.
    function test_AmountZeroSeal_Closed_RevertsAndRecordsNothing() public {
        bytes32 task2 = keccak256("task-zero");
        SincorSettlementHub.SealAuthorization memory seal = SincorSettlementHub.SealAuthorization({
            taskId: task2,
            buyer: buyer,
            provider: payee,
            amount: 0,
            expectedSchemaHash: keccak256("schema-v1"),
            sigilCommitment: keccak256("sigil-zero"),
            deadline: block.timestamp + 1 hours,
            nonce: 0
        });
        (uint8 v, bytes32 r, bytes32 s) = _signSeal(seal, buyerPk);
        uint256 buyerBefore = usdc.balanceOf(buyer);

        vm.prank(attacker); // anyone may try to submit
        vm.expectRevert(SincorSettlementHub.SealAmountZero.selector);
        hub.verifyAndSettleSeal(seal, v, r, s);

        assertFalse(hub.isTaskVerified(task2, keccak256("sigil-zero")), "$0 seal records nothing");
        assertEq(usdc.balanceOf(buyer), buyerBefore, "buyer balance untouched");
        assertEq(hub.nonces(buyer), 0, "nonce not consumed by the reverted seal");

        // The oracle's verified path rejects it: no verified record exists.
        address agent = address(0xA6E7);
        vm.expectRevert(SincorValidationReputationOracle.SettlementNotVerified.selector);
        oracle.recordVerifiedSettlement(agent, task2, keccak256("sigil-zero"), SincorValidationReputationOracle.Outcome.Success);
        (int256 score, uint256 successes,,) = oracle.reputationOf(agent);
        assertEq(score, 0, "no reputation for a $0 settlement");
        assertEq(successes, 0, "no 'verified' settlement counted");
    }

    /// @notice Zero sigil inside the signed struct still fails closed.
    function test_ZeroSigilInStruct_RevertsSigilCommitmentZero() public {
        SincorSettlementHub.SealAuthorization memory seal = _seal(100_000_000, 0, bytes32(0));
        (uint8 v, bytes32 r, bytes32 s) = _signSeal(seal, buyerPk);
        vm.expectRevert(SincorSettlementHub.SigilCommitmentZero.selector);
        hub.verifyAndSettleSeal(seal, v, r, s);
        assertEq(hub.nonces(buyer), 0, "nonce not consumed");
    }
}
