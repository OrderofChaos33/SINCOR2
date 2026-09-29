// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title P23 CollectionRegistry — NFT collection whitelist with 48 h timelock
/// @notice Adds/removals execute only after a 48-hour delay. Removal freezes
///         new deposits immediately (the vault reads the active set at deposit
///         time); existing positions keep full redemption rights.
/// @dev Self-contained (no OpenZeppelin imports) so it compiles with the bare
///      solc 0.8.24 binary. Production deploy should use OZ Ownable/Timelock.
contract CollectionRegistry {
    uint256 public constant TIMELOCK = 48 hours;

    struct TimelockOp {
        bool isAdd;
        address collection;
        uint64 queuedAt;
        bool executed;
    }

    mapping(address => bool) public whitelisted;
    mapping(bytes32 => TimelockOp) public ops;
    uint256 public opCount;
    address public admin;

    event OpQueued(bytes32 indexed opId, bool isAdd, address indexed collection, uint64 executableAt);
    event OpExecuted(bytes32 indexed opId, bool isAdd, address indexed collection);

    error NotAdmin();
    error Timelocked();
    error AlreadyExecuted();
    error UnknownOp();
    error BadCollection();

    constructor(address _admin) {
        admin = _admin;
    }

    function queueAdd(address collection) external returns (bytes32 opId) {
        return _queue(true, collection);
    }

    function queueRemove(address collection) external returns (bytes32 opId) {
        return _queue(false, collection);
    }

    function _queue(bool isAdd, address collection) internal returns (bytes32 opId) {
        if (msg.sender != admin) revert NotAdmin();
        if (collection == address(0)) revert BadCollection();
        opId = keccak256(abi.encode(++opCount, isAdd, collection, block.timestamp));
        ops[opId] = TimelockOp({
            isAdd: isAdd,
            collection: collection,
            queuedAt: uint64(block.timestamp),
            executed: false
        });
        emit OpQueued(opId, isAdd, collection, uint64(block.timestamp + TIMELOCK));
    }

    function execute(bytes32 opId) external {
        if (msg.sender != admin) revert NotAdmin();
        TimelockOp storage op = ops[opId];
        if (op.queuedAt == 0) revert UnknownOp();
        if (op.executed) revert AlreadyExecuted();
        if (block.timestamp < op.queuedAt + TIMELOCK) revert Timelocked();
        op.executed = true;
        if (op.isAdd) {
            whitelisted[op.collection] = true;
        } else {
            whitelisted[op.collection] = false;
        }
        emit OpExecuted(opId, op.isAdd, op.collection);
    }

    function isWhitelisted(address collection) external view returns (bool) {
        return whitelisted[collection];
    }
}
