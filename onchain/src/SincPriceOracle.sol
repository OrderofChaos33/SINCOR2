// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {FullMath} from "@uniswap/v4-core/src/libraries/FullMath.sol";
import {ISincPriceOracle} from "./interfaces/ISincLoop.sol";
import {IChainlinkAggregatorV3, ISincCurvePrice} from "./interfaces/ISincLoopInfra.sol";

/// @title SincPriceOracle — production SINC/USDC price oracle
/// @notice Upgraded successor to sinc-liquidity-pipeline `OracleRouter.sol`, hardened and
///         narrowed to the ISincPriceOracle surface consumed by SINCLending.
///
///         CURVE mode (default):
///           price = SincBondingCurve.currentPriceWei()  [wei ETH per whole SINC]
///                 × Chainlink ETH/USD                    [8 dp]
///                 → USDC (6 dp) per whole SINC, scaled 1e6 (ISincPriceOracle spec).
///         MANUAL mode (post-graduation / emergency):
///           admin-pushed price with its own freshness heartbeat.
///
///         Safety: Chainlink staleness heartbeat, positive-answer check, and
///         admin-tunable [minPrice, maxPrice] sanity bounds on every read.
contract SincPriceOracle is AccessControl, ISincPriceOracle {
    bytes32 public constant ORACLE_ADMIN_ROLE = keccak256("ORACLE_ADMIN_ROLE");

    enum Source {
        CURVE,
        MANUAL
    }

    ISincCurvePrice public curve;
    IChainlinkAggregatorV3 public ethUsdFeed;
    uint256 public heartbeat; // max age of a Chainlink answer

    Source public source;
    uint256 public manualPrice; // 6 dp per whole SINC
    uint256 public manualUpdatedAt;
    uint256 public manualHeartbeat;

    uint256 public minPrice; // sanity bounds, 6 dp per whole SINC
    uint256 public maxPrice;

    event SourceUpdated(Source source);
    event CurveUpdated(address curve);
    event FeedUpdated(address feed, uint256 heartbeat);
    event ManualPriceUpdated(uint256 price, uint256 updatedAt);
    event ManualHeartbeatUpdated(uint256 heartbeat);
    event BoundsUpdated(uint256 minPrice, uint256 maxPrice);

    error ZeroAddress();
    error StalePrice();
    error InvalidPrice();
    error OutOfBounds(uint256 price);
    error BadBounds();

    /// @param admin        Receives DEFAULT_ADMIN_ROLE + ORACLE_ADMIN_ROLE.
    /// @param _curve       SincBondingCurve (Base: 0x75dE341a2BC81806198364F125d4Cde36527619C).
    /// @param _ethUsdFeed  Chainlink ETH/USD (Base: 0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70).
    /// @param _heartbeat   Max Chainlink answer age in seconds (0 → 1 hour).
    constructor(address admin, address _curve, address _ethUsdFeed, uint256 _heartbeat) {
        if (admin == address(0) || _curve == address(0) || _ethUsdFeed == address(0)) {
            revert ZeroAddress();
        }
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(ORACLE_ADMIN_ROLE, admin);

        curve = ISincCurvePrice(_curve);
        ethUsdFeed = IChainlinkAggregatorV3(_ethUsdFeed);
        heartbeat = _heartbeat == 0 ? 1 hours : _heartbeat;

        source = Source.CURVE;
        manualHeartbeat = 24 hours;
        minPrice = 1; // $0.000001
        maxPrice = 100_000_000; // $100.00
    }

    /// @inheritdoc ISincPriceOracle
    function sincPriceUSDC() external view returns (uint256) {
        uint256 p;
        if (source == Source.MANUAL) {
            if (block.timestamp - manualUpdatedAt > manualHeartbeat) revert StalePrice();
            p = manualPrice;
        } else {
            uint256 priceWei = curve.currentPriceWei(); // wei ETH per whole SINC
            uint256 ethUsd = _ethUsd(); // 8 dp USD per ETH
            // (wei/SINC × usd8/ETH) / 1e20 = usd6 per whole SINC
            p = FullMath.mulDiv(priceWei, ethUsd, 1e20);
        }
        if (p < minPrice || p > maxPrice) revert OutOfBounds(p);
        return p;
    }

    function _ethUsd() internal view returns (uint256) {
        // slither-disable-next-line unused-return -- startedAt is not a freshness signal;
        // roundId/answeredInRound/updatedAt are all consumed below.
        (uint80 roundId, int256 answer,, uint256 updatedAt, uint80 answeredInRound) = ethUsdFeed.latestRoundData();
        if (answer <= 0) revert InvalidPrice();
        if (answeredInRound < roundId) revert StalePrice(); // round not finalized
        if (block.timestamp - updatedAt > heartbeat) revert StalePrice();
        return uint256(answer);
    }

    // ----------------------------- admin -----------------------------

    function setSource(Source s) external onlyRole(ORACLE_ADMIN_ROLE) {
        source = s;
        emit SourceUpdated(s);
    }

    function setCurve(address _curve) external onlyRole(ORACLE_ADMIN_ROLE) {
        if (_curve == address(0)) revert ZeroAddress();
        curve = ISincCurvePrice(_curve);
        emit CurveUpdated(_curve);
    }

    function setEthUsdFeed(address _feed, uint256 _heartbeat) external onlyRole(ORACLE_ADMIN_ROLE) {
        if (_feed == address(0)) revert ZeroAddress();
        ethUsdFeed = IChainlinkAggregatorV3(_feed);
        heartbeat = _heartbeat;
        emit FeedUpdated(_feed, _heartbeat);
    }

    /// @notice Push a manual price (6 dp per whole SINC) and arm MANUAL mode freshness.
    function setManualPrice(uint256 p) external onlyRole(ORACLE_ADMIN_ROLE) {
        manualPrice = p;
        manualUpdatedAt = block.timestamp;
        emit ManualPriceUpdated(p, block.timestamp);
    }

    function setManualHeartbeat(uint256 hb) external onlyRole(ORACLE_ADMIN_ROLE) {
        manualHeartbeat = hb;
        emit ManualHeartbeatUpdated(hb);
    }

    function setBounds(uint256 _min, uint256 _max) external onlyRole(ORACLE_ADMIN_ROLE) {
        if (_min == 0 || _min >= _max) revert BadBounds();
        minPrice = _min;
        maxPrice = _max;
        emit BoundsUpdated(_min, _max);
    }
}
