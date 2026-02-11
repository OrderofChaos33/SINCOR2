// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title SINCPredictionMarket
 * @dev AI-driven prediction market for SINC ecosystem
 */
contract SINCPredictionMarket is Ownable, ReentrancyGuard {
    IERC20 public sincToken;

    struct Market {
        string question;
        uint256 deadline;
        bool resolved;
        uint8 winningOutcome; // 0 for NO, 1 for YES
        uint256 totalYesPool;
        uint256 totalNoPool;
        mapping(address => uint256) yesBets;
        mapping(address => uint256) noBets;
    }

    mapping(uint256 => Market) public markets;
    uint256 public nextMarketId = 1;

    event MarketCreated(uint256 indexed marketId, string question, uint256 deadline);
    event BetPlaced(uint256 indexed marketId, address indexed user, bool outcome, uint256 amount);
    event MarketResolved(uint256 indexed marketId, uint8 winningOutcome);
    event WinningsClaimed(uint256 indexed marketId, address indexed user, uint256 amount);

    constructor(address _sincToken) Ownable(msg.sender) {
        sincToken = IERC20(_sincToken);
    }

    /**
     * @dev Create a new prediction market
     */
    function createMarket(string calldata question, uint256 duration) external onlyOwner returns (uint256) {
        uint256 marketId = nextMarketId++;
        Market storage market = markets[marketId];
        market.question = question;
        market.deadline = block.timestamp + duration;
        market.resolved = false;

        emit MarketCreated(marketId, question, market.deadline);
        return marketId;
    }

    /**
     * @dev Place a bet on an outcome
     */
    function placeBet(uint256 marketId, bool isYes, uint256 amount) external nonReentrant {
        Market storage market = markets[marketId];
        require(!market.resolved, "Market resolved");
        require(block.timestamp < market.deadline, "Deadline passed");
        require(amount > 0, "Amount must be > 0");

        require(sincToken.transferFrom(msg.sender, address(this), amount), "Transfer failed");

        if (isYes) {
            market.yesBets[msg.sender] += amount;
            market.totalYesPool += amount;
        } else {
            market.noBets[msg.sender] += amount;
            market.totalNoPool += amount;
        }

        emit BetPlaced(marketId, msg.sender, isYes, amount);
    }

    /**
     * @dev Resolve a market
     */
    function resolveMarket(uint256 marketId, uint8 _winningOutcome) external onlyOwner {
        Market storage market = markets[marketId];
        require(!market.resolved, "Already resolved");
        require(block.timestamp >= market.deadline, "Deadline not reached");
        require(_winningOutcome < 2, "Invalid outcome");

        market.resolved = true;
        market.winningOutcome = _winningOutcome;

        emit MarketResolved(marketId, _winningOutcome);
    }

    /**
     * @dev Claim winnings
     */
    function claimWinnings(uint256 marketId) external nonReentrant {
        Market storage market = markets[marketId];
        require(market.resolved, "Not resolved");
        
        uint256 winnings = 0;
        if (market.winningOutcome == 1) { // YES won
            uint256 userBet = market.yesBets[msg.sender];
            require(userBet > 0, "No winning bet");
            // Winning formula: (userBet / totalYesPool) * totalPool
            winnings = (userBet * (market.totalYesPool + market.totalNoPool)) / market.totalYesPool;
            market.yesBets[msg.sender] = 0;
        } else { // NO won
            uint256 userBet = market.noBets[msg.sender];
            require(userBet > 0, "No winning bet");
            winnings = (userBet * (market.totalYesPool + market.totalNoPool)) / market.totalNoPool;
            market.noBets[msg.sender] = 0;
        }

        require(sincToken.transfer(msg.sender, winnings), "Transfer failed");
        emit WinningsClaimed(marketId, msg.sender, winnings);
    }
}
