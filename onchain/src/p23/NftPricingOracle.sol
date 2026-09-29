// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title P23 NftPricingOracle — 7-day TWAP floor from >= 2 sources
/// @notice Defenses: cross-sourced floor (never one source), 24 h staleness
///         breaker (deposits freeze, withdrawals stay open, last-good-price
///         served — never bricks the vault), 25% per-update clamp.
/// @dev Feed submission is restricted to whitelisted reporter addresses.
///      Self-contained for bare solc 0.8.24 compilation.
contract NftPricingOracle {
    uint256 public constant TWAP_WINDOW = 7 days;
    uint256 public constant STALENESS = 24 hours;
    uint256 public constant MAX_MOVE_BPS = 2500; // 25% per update
    uint256 public constant MIN_SOURCES = 2;
    uint256 public constant BPS = 10_000;

    struct FeedPoint {
        uint256 price; // USDC-wei (6dp) floor per NFT
        uint64 ts;
    }

    // collection => source => ring of recent points (bounded array)
    mapping(address => mapping(bytes32 => FeedPoint[])) private _feeds;
    mapping(address => bytes32[]) private _sources;
    mapping(address => mapping(bytes32 => bool)) private _sourceKnown;
    mapping(address => uint256) public lastGoodPrice;
    mapping(address => uint64) public lastUpdate;
    mapping(address => bool) public depositsFrozen;
    mapping(address => bool) public reporter;

    address public admin;

    event FeedSubmitted(address indexed collection, bytes32 indexed source, uint256 price);
    event DepositsFrozenSet(address indexed collection, bool frozen, string reason);

    error NotAdmin();
    error NotReporter();
    error BadPrice();

    constructor(address _admin) {
        admin = _admin;
    }

    function setReporter(address who, bool allowed) external {
        if (msg.sender != admin) revert NotAdmin();
        reporter[who] = allowed;
    }

    function submitFeed(address collection, bytes32 source, uint256 price) external {
        if (!reporter[msg.sender]) revert NotReporter();
        if (price == 0) revert BadPrice();
        if (!_sourceKnown[collection][source]) {
            _sourceKnown[collection][source] = true;
            _sources[collection].push(source);
        }
        FeedPoint[] storage arr = _feeds[collection][source];
        arr.push(FeedPoint({price: price, ts: uint64(block.timestamp)}));
        // Bounded history: keep the last 64 points per source.
        if (arr.length > 64) {
            for (uint256 i = 0; i < arr.length - 1; i++) arr[i] = arr[i + 1];
            arr.pop();
        }
        _refresh(collection);
        emit FeedSubmitted(collection, source, price);
    }

    function _sourceTwap(address collection, bytes32 source) internal view returns (uint256 twap, uint64 newest, bool ok) {
        FeedPoint[] storage arr = _feeds[collection][source];
        uint256 cutoff = block.timestamp - TWAP_WINDOW;
        uint256 sum;
        uint256 n;
        for (uint256 i = 0; i < arr.length; i++) {
            if (arr[i].ts >= cutoff) {
                sum += arr[i].price;
                n++;
                if (arr[i].ts > newest) newest = arr[i].ts;
            }
        }
        if (n == 0) return (0, 0, false);
        return (sum / n, newest, true);
    }

    /// @notice Recompute the published floor. Called on every feed submit;
    ///         also callable by anyone (keeper) to trip the staleness breaker.
    function _refresh(address collection) internal {
        bytes32[] storage sources = _sources[collection];
        uint256 sum;
        uint256 fresh;
        uint256 hi;
        uint256 lo = type(uint256).max;
        for (uint256 i = 0; i < sources.length; i++) {
            (uint256 twap, uint64 newest, bool ok) = _sourceTwap(collection, sources[i]);
            if (ok && block.timestamp - newest <= STALENESS) {
                sum += twap;
                fresh++;
                if (twap > hi) hi = twap;
                if (twap < lo) lo = twap;
            }
        }
        if (fresh < MIN_SOURCES) {
            _setFrozen(collection, true, "stale_or_single_source");
            return;
        }
        uint256 raw = sum / fresh;
        // Divergence watch: >30% spread freezes deposits.
        if (lo > 0 && (hi - lo) * BPS / lo > 3000) {
            _setFrozen(collection, true, "source_spread_gt_30pct");
            return;
        }
        uint256 candidate = raw;
        uint256 last = lastGoodPrice[collection];
        if (last > 0) {
            uint256 capUp = last * (BPS + MAX_MOVE_BPS) / BPS;
            uint256 capDown = last * (BPS - MAX_MOVE_BPS) / BPS;
            if (candidate > capUp) candidate = capUp;
            else if (candidate < capDown) candidate = capDown;
        }
        lastGoodPrice[collection] = candidate;
        lastUpdate[collection] = uint64(block.timestamp);
        _setFrozen(collection, false, "ok");
    }

    function poke(address collection) external {
        _refresh(collection);
    }

    function _setFrozen(address collection, bool frozen, string memory reason) internal {
        if (depositsFrozen[collection] != frozen) {
            depositsFrozen[collection] = frozen;
            emit DepositsFrozenSet(collection, frozen, reason);
        }
    }

    function floorPrice(address collection) external view returns (uint256) {
        return lastGoodPrice[collection];
    }

    function sourceCount(address collection) external view returns (uint256) {
        return _sources[collection].length;
    }
}
