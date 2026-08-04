// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {AggregatorV3Interface} from "../interfaces/AggregatorV3Interface.sol";

/// @title SincChainlinkOracle
/// @notice Morpho-compatible oracle for SINC with hard $1.50 floor + any Chainlink-style feed.
/// @dev Implements IOracle.price() scaled by 1e36.
///      Designed for SINC (8 decimals) / USDC (6 decimals) markets.
interface IOracle {
    function price() external view returns (uint256);
}

contract SincChainlinkOracle is Ownable, Pausable, IOracle {
    error InvalidPrice();
    error PriceBelowFloor();
    error StalePrice();
    error ZeroAddress();
    error InvalidRound();

    event FeedUpdated(address indexed oldFeed, address indexed newFeed);
    event PriceUpdated(uint256 indexed price, uint256 timestamp);

    // Hard floor: $1.50. Most Chainlink USD feeds use 8 decimals → 1.5e8
    uint256 public constant PRICE_FLOOR_8DEC = 150_000_000; // 1.50 * 1e8

    // Morpho scale for SINC(8) / USDC(6):
    // price_full * 10^(36 + loanDec - collDec) = price_full * 10^(36 + 6 - 8) = price_full * 1e34
    // With 8-dec feed: (feedAnswer * 1e34) / 1e8 = feedAnswer * 1e26
    uint256 public constant SCALE_FACTOR = 1e26;

    AggregatorV3Interface public feed;
    uint256 public maxStaleness = 1 hours;

    // Optional manual override (owner only) – useful until a real feed exists
    uint256 private _manualPrice; // Morpho-scaled
    uint256 private _manualTimestamp;
    bool public useManual = true; // start in manual mode

    constructor(address initialOwner, address _feed) Ownable(initialOwner) {
        if (initialOwner == address(0)) revert ZeroAddress();
        if (_feed != address(0)) {
            feed = AggregatorV3Interface(_feed);
            useManual = false;
        }
        // Start at exact floor (Morpho scale)
        _manualPrice = PRICE_FLOOR_8DEC * SCALE_FACTOR; // 1.5e8 * 1e26 = 1.5e34
        _manualTimestamp = block.timestamp;
    }

    // ==================== CORE ====================

    /// @notice Morpho IOracle – price of 1 SINC in USDC terms, scaled by 1e36
    function price() external view override returns (uint256) {
        if (useManual) {
            if (block.timestamp - _manualTimestamp > maxStaleness) revert StalePrice();
            return _manualPrice < (PRICE_FLOOR_8DEC * SCALE_FACTOR)
                ? PRICE_FLOOR_8DEC * SCALE_FACTOR
                : _manualPrice;
        }

        (, int256 answer,, uint256 updatedAt, uint80 answeredInRound) = feed.latestRoundData();
        if (answer <= 0) revert InvalidPrice();
        if (answeredInRound == 0) revert InvalidRound();
        if (block.timestamp - updatedAt > maxStaleness) revert StalePrice();

        uint256 raw = uint256(answer);

        // Enforce hard floor
        if (raw < PRICE_FLOOR_8DEC) {
            raw = PRICE_FLOOR_8DEC;
        }

        // Convert 8-dec Chainlink answer → Morpho 1e36 scale for SINC/USDC
        return raw * SCALE_FACTOR;
    }

    // ==================== ADMIN ====================

    function setFeed(address newFeed) external onlyOwner {
        if (newFeed == address(0)) revert ZeroAddress();
        address old = address(feed);
        feed = AggregatorV3Interface(newFeed);
        useManual = false;
        emit FeedUpdated(old, newFeed);
    }

    /// @notice Manual price update (Morpho-scaled). Only usable while useManual = true.
    /// @param newPrice Morpho-scaled value (e.g. 1.5e34 for $1.50)
    function updateManualPrice(uint256 newPrice) external onlyOwner whenNotPaused {
        uint256 floorValue = PRICE_FLOOR_8DEC * SCALE_FACTOR;
        if (newPrice < floorValue) revert PriceBelowFloor();
        _manualPrice = newPrice;
        _manualTimestamp = block.timestamp;
        emit PriceUpdated(newPrice, block.timestamp);
    }

    function setUseManual(bool _useManual) external onlyOwner {
        useManual = _useManual;
    }

    function setMaxStaleness(uint256 _seconds) external onlyOwner {
        maxStaleness = _seconds;
    }

    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    // ==================== VIEWS ====================

    /// @notice Human-readable price with 18 decimals (1.5e18 = $1.50)
    function getPrice() external view returns (uint256) {
        uint256 p = this.price();
        // Morpho scale → 18-dec: / 1e16
        return p / 1e16;
    }

    function floorScaled() external pure returns (uint256) {
        return PRICE_FLOOR_8DEC * SCALE_FACTOR; // 1.5e34
    }
}
