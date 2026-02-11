// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/**
 * @title SINCCarbonCredit
 * @dev Real-World Asset (RWA) Tokenization for Carbon Credits
 * Verified by SINC AI Agents
 */
contract SINCCarbonCredit is ERC20, Ownable, ReentrancyGuard {
    struct Project {
        string name;
        string location;
        uint256 totalCredits;
        uint256 issuedCredits;
        bool verified;
        string verificationUrl;
    }

    mapping(uint256 => Project) public projects;
    uint256 public nextProjectId = 1;

    event ProjectRegistered(uint256 indexed projectId, string name, uint256 totalCredits);
    event CreditsIssued(uint256 indexed projectId, address indexed receiver, uint256 amount);
    event ProjectVerified(uint256 indexed projectId, string verificationUrl);

    constructor() ERC20("SINC Carbon Credit", "SCC") Ownable(msg.sender) {}

    /**
     * @dev Register a new carbon credit project
     */
    function registerProject(
        string memory name,
        string memory location,
        uint256 totalCredits
    ) external onlyOwner returns (uint256) {
        uint256 projectId = nextProjectId++;
        projects[projectId] = Project({
            name: name,
            location: location,
            totalCredits: totalCredits,
            issuedCredits: 0,
            verified: false,
            verificationUrl: ""
        });

        emit ProjectRegistered(projectId, name, totalCredits);
        return projectId;
    }

    /**
     * @dev Verify a project (can be called by authorized AI agents or owner)
     */
    function verifyProject(uint256 projectId, string calldata verificationUrl) external onlyOwner {
        Project storage project = projects[projectId];
        require(projectId < nextProjectId, "Project does not exist");
        
        project.verified = true;
        project.verificationUrl = verificationUrl;
        
        emit ProjectVerified(projectId, verificationUrl);
    }

    /**
     * @dev Issue credits for a verified project
     */
    function issueCredits(uint256 projectId, address receiver, uint256 amount) external onlyOwner nonReentrant {
        Project storage project = projects[projectId];
        require(project.verified, "Project not verified");
        require(project.issuedCredits + amount <= project.totalCredits, "Exceeds total credits");

        project.issuedCredits += amount;
        _mint(receiver, amount);

        emit CreditsIssued(projectId, receiver, amount);
    }

    /**
     * @dev Retire credits (burning them to claim offset)
     */
    function retireCredits(uint256 amount) external nonReentrant {
        _burn(msg.sender, amount);
        // Event for retirement tracking
        emit CreditsRetired(msg.sender, amount);
    }

    event CreditsRetired(address indexed user, uint256 amount);
}
