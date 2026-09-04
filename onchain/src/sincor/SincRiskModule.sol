// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

import {AggregatorV3Interface} from "../interfaces/AggregatorV3Interface.sol";
import {ISincRiskModule} from "../interfaces/ISincVaultSystem.sol";
import {SincConstants} from "./SincConstants.sol";

contract SincRiskModule is AccessControl, Pausable, ISincRiskModule {
    bytes32 public constant RISK_ADMIN_ROLE = keccak256("RISK_ADMIN_ROLE");

    struct OracleConfig {
        address feed;
        uint32 heartbeat;
    }

    mapping(address token => OracleConfig) public oracleOf;

    uint16 public maxOracleDeviationBps;

    error InvalidConfig();
    error UnsupportedToken();
    error StaleOracle();
    error InvalidPrice();
    error SlippageTooHigh();
    error OracleDeviationTooHigh();

    constructor(address admin, uint16 _maxOracleDeviationBps) {
        if (admin == address(0) || _maxOracleDeviationBps > SincConstants.BPS_DENOM) revert InvalidConfig();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(RISK_ADMIN_ROLE, admin);
        maxOracleDeviationBps = _maxOracleDeviationBps;
    }

    function setOracle(address token, address feed, uint32 heartbeat) external onlyRole(RISK_ADMIN_ROLE) {
        if (token == address(0) || feed == address(0) || heartbeat == 0) revert InvalidConfig();
        oracleOf[token] = OracleConfig({feed: feed, heartbeat: heartbeat});
    }

    function setMaxOracleDeviationBps(uint16 bps) external onlyRole(RISK_ADMIN_ROLE) {
        if (bps > SincConstants.BPS_DENOM) revert InvalidConfig();
        maxOracleDeviationBps = bps;
    }

    function setCircuitBreaker(bool paused_) external onlyRole(RISK_ADMIN_ROLE) {
        if (paused_) {
            _pause();
        } else {
            _unpause();
        }
        emit CircuitBreakerSet(paused_);
    }

    function validateSwap(SwapCheckParams calldata p) external view whenNotPaused returns (uint256 minOut) {
        if (p.maxSlippageBps > SincConstants.MAX_SLIPPAGE_BPS) revert SlippageTooHigh();
        if (p.maxOracleDeviationBps > maxOracleDeviationBps) revert OracleDeviationTooHigh();
        if (p.expectedOutOracle == 0 || p.amountIn == 0) revert InvalidConfig();

        minOut = Math.mulDiv(
            p.expectedOutOracle,
            SincConstants.BPS_DENOM - p.maxSlippageBps,
            SincConstants.BPS_DENOM
        );
    }

    function validatePrice(address token) public view returns (uint256 price, uint256 updatedAt) {
        OracleConfig memory cfg = oracleOf[token];
        if (cfg.feed == address(0)) revert UnsupportedToken();

        AggregatorV3Interface feed = AggregatorV3Interface(cfg.feed);
        (uint80 roundId, int256 answer,, uint256 updated, uint80 answeredInRound) = feed.latestRoundData();
        if (roundId == 0 || answer <= 0 || answeredInRound < roundId) revert InvalidPrice();
        if (block.timestamp - updated > cfg.heartbeat) revert StaleOracle();

        uint8 d = feed.decimals();
        uint256 uAnswer = uint256(answer);

        if (d > 8) {
            price = uAnswer / (10 ** (d - 8));
        } else {
            price = uAnswer * (10 ** (8 - d));
        }
        updatedAt = updated;
    }

    function validateExecutionPrice(
        uint256 dexPriceE18,
        uint256 referencePriceE18,
        uint16 deviationBps
    ) external pure returns (bool ok) {
        if (referencePriceE18 == 0 || deviationBps > SincConstants.BPS_DENOM) revert InvalidConfig();
        uint256 diff = dexPriceE18 > referencePriceE18 ? dexPriceE18 - referencePriceE18 : referencePriceE18 - dexPriceE18;
        ok = Math.mulDiv(diff, SincConstants.BPS_DENOM, referencePriceE18) <= deviationBps;
    }

    function isPaused() external view returns (bool) {
        return paused();
    }
}
