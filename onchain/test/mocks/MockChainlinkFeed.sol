// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {AggregatorV3Interface} from "../../src/interfaces/AggregatorV3Interface.sol";

contract MockChainlinkFeed is AggregatorV3Interface {
    uint8 public immutable override decimals;
    string public override description;
    uint256 public override version = 1;

    uint80 public round;
    int256 public answer;
    uint256 public updatedAt;

    constructor(uint8 _decimals, string memory _description, int256 _answer) {
        decimals = _decimals;
        description = _description;
        setAnswer(_answer);
    }

    function setAnswer(int256 _answer) public {
        round += 1;
        answer = _answer;
        updatedAt = block.timestamp;
    }

    function getRoundData(uint80 _roundId)
        external
        view
        returns (uint80 roundId, int256 _answer, uint256 startedAt, uint256 _updatedAt, uint80 answeredInRound)
    {
        require(_roundId <= round && _roundId != 0, "NO_ROUND");
        return (_roundId, answer, updatedAt, updatedAt, _roundId);
    }

    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 _answer, uint256 startedAt, uint256 _updatedAt, uint80 answeredInRound)
    {
        return (round, answer, updatedAt, updatedAt, round);
    }
}
