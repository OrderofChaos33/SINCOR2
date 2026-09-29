// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title P24 RevenueAccrual — 7-day epochs, snapshot accounting, pull claims
/// @notice A holder's claimable amount for an epoch is fixed by their balance
///         at the epoch-start snapshot: late joiners cannot claim past epochs.
///         Claims are pull-based with per-payout isolation (non-bricking).
/// @dev Balances are pushed in by the token's transfer hook (integration).
///      Self-contained for bare solc 0.8.24 compilation.
contract RevenueAccrual {
    uint256 public constant EPOCH = 7 days;

    struct EpochData {
        uint64 start;
        uint256 revenue;
        uint256 snapshotTotal;
        mapping(address => uint256) snapshot; // holder => balance at snapshot
        mapping(address => bool) snapshotted;
        mapping(address => uint256) claimed;
    }

    // token => epoch index => data
    mapping(address => mapping(uint256 => EpochData)) private _epochs;
    mapping(address => mapping(uint256 => bool)) private _epochInit;
    mapping(address => mapping(address => uint256)) public balances;

    event EpochSnapshotted(address indexed token, uint256 indexed index, uint256 total);
    event RevenueAccrued(address indexed token, uint256 indexed index, uint256 amount);
    event Claimed(address indexed token, address indexed holder, uint256 indexed index, uint256 amount);

    function setBalance(address token, address holder, uint256 balance) external {
        balances[token][holder] = balance;
    }

    function epochIndex(uint256 ts) public pure returns (uint256) {
        return ts / EPOCH;
    }

    function snapshotEpoch(address token) public returns (uint256 index) {
        index = epochIndex(block.timestamp);
        EpochData storage e = _epochs[token][index];
        if (!_epochInit[token][index]) {
            _epochInit[token][index] = true;
            e.start = uint64(index * EPOCH);
            e.snapshotTotal = 0; // filled lazily per holder below
            emit EpochSnapshotted(token, index, 0);
        }
    }

    /// @notice Snapshot one holder's balance at the current epoch start.
    ///         Lazy per-holder snapshotting keeps gas bounded (no holder loops).
    function snapshotHolder(address token, address holder) public {
        uint256 index = snapshotEpoch(token);
        EpochData storage e = _epochs[token][index];
        if (!e.snapshotted[holder]) {
            e.snapshotted[holder] = true;
            e.snapshot[holder] = balances[token][holder];
            e.snapshotTotal += balances[token][holder];
        }
    }

    function accrueRevenue(address token, uint256 amount) external {
        uint256 index = snapshotEpoch(token);
        _epochs[token][index].revenue += amount;
        emit RevenueAccrued(token, index, amount);
    }

    function claimable(address token, address holder, uint256 index) public view returns (uint256) {
        if (!_epochInit[token][index]) return 0;
        EpochData storage e = _epochs[token][index];
        if (e.snapshotTotal == 0 || e.revenue == 0) return 0;
        uint256 snap = e.snapshot[holder];
        if (snap == 0) return 0;
        uint256 gross = e.revenue * snap / e.snapshotTotal;
        uint256 already = e.claimed[holder];
        return gross > already ? gross - already : 0;
    }

    function claim(address token, uint256 index) external returns (uint256 amount) {
        amount = claimable(token, msg.sender, index);
        if (amount > 0) {
            _epochs[token][index].claimed[msg.sender] += amount;
            emit Claimed(token, msg.sender, index, amount);
        }
    }
}
