// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SigilPolicyModule} from "../contracts/modules/SigilPolicyModule.sol";

/// @notice W-191 (hunt 53) regression tests: a fund release nested inside a
///         wrapper call (multicall / aggregate family) must face exactly the
///         policy gate a direct call faces. Before the fix, preCheck decoded
///         only the outermost selector, so a `transfer` wrapped in
///         `multicall(bytes[])` passed ungated with NO attestation at all
///         (PoC poc53_escrow_obs_sigil.py §C0/C1). Gate-level, like the PoC:
///         these tests exercise preCheck directly (msg.sender = account).

contract W191Token is ERC20 {
    constructor() ERC20("W191 Mock", "W191") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

/// @dev Minimal ERC-7579-style account mirroring SigilPolicyModule.t.sol:
///      installs the hook and runs preCheck before every execute().
contract W191Account {
    SigilPolicyModule public hook;
    address public sessionKey;

    constructor(SigilPolicyModule hook_, address sessionKey_) {
        hook = hook_;
        sessionKey = sessionKey_;
    }

    function installHook(bytes calldata initData) external {
        hook.onInstall(initData);
    }

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

contract SigilPolicyModuleW191Test is Test {
    // Wrapper selectors (verified against keccak256 of the signatures).
    bytes4 internal constant MULTICALL = 0xac9650d8; // multicall(bytes[])
    bytes4 internal constant MULTICALL_DEADLINE = 0x5ae401dc; // multicall(uint256,bytes[])
    bytes4 internal constant AGGREGATE = 0x252dba42; // aggregate((address,bytes)[])
    bytes4 internal constant TRY_AGGREGATE = 0xbce38bd7; // tryAggregate(bool,(address,bytes)[])
    bytes4 internal constant AGGREGATE3 = 0x82ad56cb; // aggregate3((address,bool,bytes)[])
    bytes4 internal constant AGGREGATE3_VALUE = 0x174dea71; // aggregate3Value((address,bool,uint256,bytes)[])

    struct AggCall {
        address target;
        bytes callData;
    }

    struct Agg3Call {
        address target;
        bool allowFailure;
        bytes callData;
    }

    struct Agg3ValueCall {
        address target;
        bool allowFailure;
        uint256 value;
        bytes callData;
    }

    SigilPolicyModule public module;
    W191Account public account;
    W191Token public token;

    uint256 public verifierPk = 0xA11CE5;
    address public verifier;
    address public sessionKey = address(0x5E5510);
    address public payee = address(0x9A11);
    address public spender = address(0x59E9DE12);
    /// @dev Stand-in for a router / multicall contract used as the outer
    ///      execution target, exactly as in the PoC (target = holder/router).
    address public router = address(0xD00D);
    address public aggregator = address(0xA661);

    uint256 public constant MAX_CAP = 10_000_000;
    uint256 public constant THRESHOLD = 100; // mirrors the PoC install
    uint256 public nonceCounter;

    function setUp() public {
        verifier = vm.addr(verifierPk);
        module = new SigilPolicyModule();
        account = new W191Account(module, sessionKey);
        token = new W191Token();
        vm.deal(address(account), 100 ether);
        token.mint(address(account), 10_000_000);

        vm.prank(address(account));
        account.installHook(abi.encode(verifier, MAX_CAP, THRESHOLD, address(0)));
    }

    // ------------------------------------------------------------- helpers

    function _att(address token_, address payee_, uint256 cap)
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
            expiry: uint64(block.timestamp + 1 hours),
            nonce: ++nonceCounter
        });
    }

    function _registerValid(address token_, address payee_, uint256 cap)
        internal
        returns (SigilPolicyModule.SigilAttestation memory att)
    {
        att = _att(token_, payee_, cap);
        bytes32 digest = module.attestationDigest(att);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(verifierPk, digest);
        module.registerAttestation(att, abi.encodePacked(r, s, v));
    }

    function _packed(address target, uint256 value, bytes memory inner) internal pure returns (bytes memory) {
        return abi.encodePacked(target, value, inner);
    }

    function _transfer(address to, uint256 amount) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(IERC20.transfer.selector, to, amount);
    }

    function _multicall(bytes[] memory calls) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(MULTICALL, calls);
    }

    function _preCheck(bytes memory packedExec) internal returns (bytes memory) {
        vm.prank(address(account));
        return module.preCheck(address(0), 0, packedExec);
    }

    function _pendingExists(address token_, address payee_) internal view returns (bool exists) {
        (,,,,,, exists) = module.pendingOf(address(account), token_, payee_);
    }

    // ------------------------------------------------- PoC §C0/C1 (hunt 53)

    /// PoC §C0 control: the direct transfer IS gated.
    function test_W191_ControlDirectTransferBlocked() public {
        vm.expectRevert(
            abi.encodeWithSelector(
                SigilPolicyModule.NoValidAttestation.selector, address(account), address(token), payee
            )
        );
        _preCheck(_packed(address(token), 0, _transfer(payee, 500_000)));
    }

    /// PoC §C1 REPRODUCTION: the identical transfer wrapped in
    /// multicall(bytes[]) passed preCheck ungated before the fix.
    function test_W191_MulticallNestedTransferBlocked() public {
        bytes[] memory calls = new bytes[](1);
        calls[0] = _transfer(payee, 500_000);
        // multicall(bytes[]) elements are self-calls on the wrapper target,
        // so the nested transfer decodes as a release of the target "token"
        // (the router stand-in) to payee — and must be gated as such.
        vm.expectRevert(
            abi.encodeWithSelector(
                SigilPolicyModule.NoValidAttestation.selector, address(account), router, payee
            )
        );
        _preCheck(_packed(router, 0, _multicall(calls)));
    }

    /// Same reproduction end-to-end: execute() must revert at the hook and
    /// no funds move.
    function test_W191_MulticallNestedTransferBlockedEndToEnd() public {
        bytes[] memory calls = new bytes[](1);
        calls[0] = _transfer(payee, 500_000);
        vm.prank(sessionKey);
        vm.expectRevert();
        account.execute(_packed(router, 0, _multicall(calls)));
        assertEq(token.balanceOf(payee), 0);
    }

    // ------------------------------------------- aggregate family (explicit
    // per-call targets — the nested transfer keeps its real token/payee)

    function test_W191_AggregateNestedTransferBlocked() public {
        AggCall[] memory calls = new AggCall[](1);
        calls[0] = AggCall({target: address(token), callData: _transfer(payee, 500_000)});
        vm.expectRevert(
            abi.encodeWithSelector(
                SigilPolicyModule.NoValidAttestation.selector, address(account), address(token), payee
            )
        );
        _preCheck(_packed(aggregator, 0, abi.encodeWithSelector(AGGREGATE, calls)));
    }

    function test_W191_TryAggregateNestedTransferBlocked() public {
        AggCall[] memory calls = new AggCall[](1);
        calls[0] = AggCall({target: address(token), callData: _transfer(payee, 500_000)});
        vm.expectRevert(
            abi.encodeWithSelector(
                SigilPolicyModule.NoValidAttestation.selector, address(account), address(token), payee
            )
        );
        _preCheck(_packed(aggregator, 0, abi.encodeWithSelector(TRY_AGGREGATE, false, calls)));
    }

    function test_W191_Aggregate3NestedTransferBlocked() public {
        Agg3Call[] memory calls = new Agg3Call[](1);
        calls[0] = Agg3Call({target: address(token), allowFailure: false, callData: _transfer(payee, 500_000)});
        vm.expectRevert(
            abi.encodeWithSelector(
                SigilPolicyModule.NoValidAttestation.selector, address(account), address(token), payee
            )
        );
        _preCheck(_packed(aggregator, 0, abi.encodeWithSelector(AGGREGATE3, calls)));
    }

    function test_W191_Aggregate3ValueNestedTransferBlocked() public {
        Agg3ValueCall[] memory calls = new Agg3ValueCall[](1);
        calls[0] = Agg3ValueCall({
            target: address(token), allowFailure: false, value: 0, callData: _transfer(payee, 500_000)
        });
        vm.expectRevert(
            abi.encodeWithSelector(
                SigilPolicyModule.NoValidAttestation.selector, address(account), address(token), payee
            )
        );
        _preCheck(_packed(aggregator, 0, abi.encodeWithSelector(AGGREGATE3_VALUE, calls)));
    }

    function test_W191_MulticallDeadlineNestedTransferBlocked() public {
        bytes[] memory calls = new bytes[](1);
        calls[0] = _transfer(payee, 500_000);
        vm.expectRevert(
            abi.encodeWithSelector(
                SigilPolicyModule.NoValidAttestation.selector, address(account), router, payee
            )
        );
        _preCheck(
            _packed(router, 0, abi.encodeWithSelector(MULTICALL_DEADLINE, block.timestamp + 1 hours, calls))
        );
    }

    // ------------------------------------------------------- allowed nested

    /// A policy-allowed nested transfer (valid attestation, aggregate3
    /// form) passes and consumes the attestation exactly like a direct one.
    function test_W191_Aggregate3NestedTransferWithAttestationPasses() public {
        SigilPolicyModule.SigilAttestation memory att = _registerValid(address(token), payee, 500_000);
        Agg3Call[] memory calls = new Agg3Call[](1);
        calls[0] = Agg3Call({target: address(token), allowFailure: false, callData: _transfer(payee, 500_000)});
        bytes memory hookData = _preCheck(_packed(aggregator, 0, abi.encodeWithSelector(AGGREGATE3, calls)));
        assertEq(hookData, abi.encode(module.attestationDigest(att)));
        assertFalse(_pendingExists(address(token), payee)); // consumed, single-use
    }

    /// A policy-allowed nested transfer (valid attestation, multicall form)
    /// passes; the attestation's token slot is the wrapper target, matching
    /// the module's own decode convention (token := decoded call target).
    function test_W191_MulticallNestedTransferWithAttestationPasses() public {
        SigilPolicyModule.SigilAttestation memory att = _registerValid(router, payee, 500_000);
        bytes[] memory calls = new bytes[](1);
        calls[0] = _transfer(payee, 500_000);
        bytes memory hookData = _preCheck(_packed(router, 0, _multicall(calls)));
        assertEq(hookData, abi.encode(module.attestationDigest(att)));
        assertFalse(_pendingExists(router, payee));
    }

    /// Below the unverified threshold, a nested release is policy-allowed
    /// without an attestation — the same rule direct calls get.
    function test_W191_MulticallNestedBelowThresholdPasses() public {
        bytes[] memory calls = new bytes[](1);
        calls[0] = _transfer(payee, THRESHOLD); // 100 <= threshold
        bytes memory hookData = _preCheck(_packed(router, 0, _multicall(calls)));
        assertEq(hookData, bytes(""));
    }

    /// Every element is gated, not just the first: a benign first element
    /// does not launder a forbidden second one.
    function test_W191_MulticallSecondElementBlocked() public {
        bytes[] memory calls = new bytes[](2);
        calls[0] = _transfer(payee, THRESHOLD); // allowed on its own
        calls[1] = _transfer(payee, 500_000); // forbidden without attestation
        vm.expectRevert(
            abi.encodeWithSelector(
                SigilPolicyModule.NoValidAttestation.selector, address(account), router, payee
            )
        );
        _preCheck(_packed(router, 0, _multicall(calls)));
    }

    // ------------------------------------------------------ nested grants

    /// W-148 grant gating applies inside wrappers too: a nested approve is
    /// a release in substance and must be gated on the resulting allowance.
    function test_W191_MulticallNestedApproveBlocked() public {
        bytes[] memory calls = new bytes[](1);
        calls[0] = abi.encodeWithSelector(IERC20.approve.selector, spender, 500_000);
        vm.expectRevert(
            abi.encodeWithSelector(
                SigilPolicyModule.NoValidAttestation.selector, address(account), router, spender
            )
        );
        _preCheck(_packed(router, 0, _multicall(calls)));
    }

    // ------------------------------------------------- recursion & safety

    /// A multicall inside a multicall is decoded recursively and gated.
    function test_W191_DoubleNestedMulticallBlocked() public {
        bytes[] memory inner = new bytes[](1);
        inner[0] = _transfer(payee, 500_000);
        bytes[] memory outer = new bytes[](1);
        outer[0] = _multicall(inner);
        vm.expectRevert(
            abi.encodeWithSelector(
                SigilPolicyModule.NoValidAttestation.selector, address(account), router, payee
            )
        );
        _preCheck(_packed(router, 0, _multicall(outer)));
    }

    /// A known wrapper selector with a malformed payload fails closed —
    /// it is never waved through unexamined.
    function test_W191_MalformedMulticallFailsClosed() public {
        bytes memory bad = abi.encodePacked(MULTICALL, uint256(0xFFFF), uint256(7));
        vm.expectRevert(SigilPolicyModule.MalformedExecution.selector);
        _preCheck(_packed(router, 0, bad));
    }

    /// Wrapper nesting beyond the decode depth fails closed.
    function test_W191_ExcessivelyNestedMulticallFailsClosed() public {
        bytes memory payload = _transfer(payee, 500_000);
        for (uint256 i = 0; i < 6; i++) {
            bytes[] memory calls = new bytes[](1);
            calls[0] = payload;
            payload = _multicall(calls);
        }
        vm.expectRevert(SigilPolicyModule.MalformedExecution.selector);
        _preCheck(_packed(router, 0, payload));
    }
}
