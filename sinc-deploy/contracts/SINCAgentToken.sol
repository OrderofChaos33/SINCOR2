// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import "@openzeppelin/contracts/utils/Pausable.sol";
import "@openzeppelin/contracts/utils/Strings.sol";

/**
 * SINC Agent Ontology Token
 *
 * Non-transferable Soulbound Token (SBT) for agent permissions and lifecycle management.
 *
 * Features:
 * - Non-transferable (SBT-like)
 * - Erasable/revocable by owner
 * - Enforced at execution time
 * - Reentrancy protection
 * - Emergency pause capability
 * - Batch deployment
 * - EIP-4906 compliant metadata updates
 */
contract SINCAgentToken is ERC721, Ownable, ReentrancyGuard, Pausable {
    using Strings for uint256;

    struct AgentProfile {
        string purpose;           // Agent's role/purpose
        string[] permissions;     // Granted permissions
        uint256 scope;           // Access scope level
        uint256 budget;          // Budget allowance (in wei)
        uint256 lifecycleStart;  // Birth timestamp
        uint256 lifecycleEnd;    // Expiration timestamp
        bool active;             // Active status
        address assignedAgent;   // Agent wallet address
    }

    mapping(uint256 => AgentProfile) public agentProfiles;
    mapping(address => uint256) public agentTokens; // Agent address to token ID
    mapping(uint256 => uint256) public agentReputation; // 0-1000 scale (1000 = 100%)
    uint256 private _nextTokenId = 1;

    // EIP-4906 Metadata Update Event
    event MetadataUpdate(uint256 _tokenId);
    event BatchMetadataUpdate(uint256 _fromTokenId, uint256 _toTokenId);

    event AgentBorn(uint256 indexed tokenId, address indexed agent, string purpose);
    event AgentTerminated(uint256 indexed tokenId, address indexed agent);
    event PermissionsBurned(uint256 indexed tokenId);
    event AgentReassigned(uint256 indexed oldTokenId, uint256 indexed newTokenId, address indexed agent);
    event ReputationUpdated(uint256 indexed tokenId, uint256 newScore);

    constructor() ERC721("SINC Agent Token", "SAGT") Ownable(msg.sender) {}

    /**
     * @dev Override transfer functions to make tokens non-transferable
     */
    /**
     * @dev Override update to make tokens non-transferable (Soulbound)
     */
    function _update(address to, uint256 tokenId, address auth) internal virtual override returns (address) {
        address from = _ownerOf(tokenId);
        if (from != address(0) && to != address(0)) {
            revert("SINC Agent Tokens are non-transferable (Soulbound)");
        }
        return super._update(to, tokenId, auth);
    }

    /**
     * @dev Mint a new agent token with specified profile
     */
    function mintAgent(
        address agent,
        string memory purpose,
        string[] memory permissions,
        uint256 scope,
        uint256 budget,
        uint256 lifecycleDuration
    ) public onlyOwner whenNotPaused nonReentrant returns (uint256) {
        require(agentTokens[agent] == 0, "Agent already has an active token");

        uint256 tokenId = _nextTokenId++;
        _mint(address(this), tokenId); // Mint to contract, not agent

        agentProfiles[tokenId] = AgentProfile({
            purpose: purpose,
            permissions: permissions,
            scope: scope,
            budget: budget,
            lifecycleStart: block.timestamp,
            lifecycleEnd: block.timestamp + lifecycleDuration,
            active: true,
            assignedAgent: agent
        });

        agentTokens[agent] = tokenId;
        agentReputation[tokenId] = 500; // Initialize at mid-point (50%)

        emit AgentBorn(tokenId, agent, purpose);
        emit MetadataUpdate(tokenId);
        return tokenId;
    }

    /**
     * @dev Batch mint multiple agents
     */
    function mintAgentBatch(
        address[] calldata agents,
        string[] calldata purposes,
        string[][] calldata permissionsList,
        uint256[] calldata scopes,
        uint256[] calldata budgets,
        uint256[] calldata lifecycleDurations
    ) external onlyOwner whenNotPaused nonReentrant returns (uint256[] memory) {
        require(agents.length == purposes.length && 
                agents.length == permissionsList.length &&
                agents.length == scopes.length &&
                agents.length == budgets.length &&
                agents.length == lifecycleDurations.length, 
                "Array lengths mismatch");

        uint256[] memory tokenIds = new uint256[](agents.length);
        uint256 startTokenId = _nextTokenId;

        for (uint256 i = 0; i < agents.length; i++) {
            tokenIds[i] = mintAgent(
                agents[i], 
                purposes[i], 
                permissionsList[i], 
                scopes[i], 
                budgets[i], 
                lifecycleDurations[i]
            );
        }

        emit BatchMetadataUpdate(startTokenId, _nextTokenId - 1);
        return tokenIds;
    }

    /**
     * @dev Terminate agent and burn token
     */
    function terminateAgent(uint256 tokenId) external whenNotPaused nonReentrant {
        require(ownerOf(tokenId) == address(this), "Token not found");
        AgentProfile storage profile = agentProfiles[tokenId];
        require(profile.active, "Agent already terminated");
        require(msg.sender == owner() || msg.sender == profile.assignedAgent, "Unauthorized");

        profile.active = false;
        delete agentTokens[profile.assignedAgent];
        _burn(tokenId);

        emit AgentTerminated(tokenId, profile.assignedAgent);
        emit PermissionsBurned(tokenId);
        emit MetadataUpdate(tokenId);
    }

    /**
     * @dev Emergency pause
     */
    function pause() external onlyOwner {
        _pause();
    }

    /**
     * @dev Unpause
     */
    function unpause() external onlyOwner {
        _unpause();
    }

    /**
     * @dev Reassign agent with new token (rebirth)
     */
    function reassignAgent(
        address agent,
        string memory newPurpose,
        string[] memory newPermissions,
        uint256 newScope,
        uint256 newBudget,
        uint256 newLifecycleDuration
    ) external onlyOwner whenNotPaused nonReentrant returns (uint256) {
        uint256 oldTokenId = agentTokens[agent];
        if (oldTokenId != 0) {
            // Terminate old token
            AgentProfile storage oldProfile = agentProfiles[oldTokenId];
            oldProfile.active = false;
            delete agentTokens[agent];
            _burn(oldTokenId);
            emit AgentTerminated(oldTokenId, agent);
        }

        // Mint new token
        uint256 newTokenId = mintAgent(agent, newPurpose, newPermissions, newScope, newBudget, newLifecycleDuration);
        emit AgentReassigned(oldTokenId, newTokenId, agent);
        return newTokenId;
    }

    /**
     * @dev Check if agent has valid permissions
     */
    function validateAgent(address agent, string memory requiredPermission) external view returns (bool) {
        uint256 tokenId = agentTokens[agent];
        if (tokenId == 0) return false;

        AgentProfile storage profile = agentProfiles[tokenId];
        if (!profile.active) return false;
        if (block.timestamp > profile.lifecycleEnd) return false;

        for (uint256 i = 0; i < profile.permissions.length; i++) {
            if (keccak256(abi.encodePacked(profile.permissions[i])) == keccak256(abi.encodePacked(requiredPermission))) {
                return true;
            }
        }
        return false;
    }

    /**
     * @dev Get agent profile
     */
    function getAgentProfile(uint256 tokenId) external view returns (AgentProfile memory) {
        require(_ownerOf(tokenId) != address(0), "Token does not exist");
        return agentProfiles[tokenId];
    }

    /**
     * @dev Get agent token ID
     */
    function getAgentToken(address agent) external view returns (uint256) {
        return agentTokens[agent];
    }

    /**
     * @dev Check if agent is active
     */
    function isAgentActive(address agent) external view returns (bool) {
        uint256 tokenId = agentTokens[agent];
        if (tokenId == 0) return false;
        return agentProfiles[tokenId].active && block.timestamp <= agentProfiles[tokenId].lifecycleEnd;
    }

    /**
     * @dev Emergency revoke all permissions for an agent
     */
    function emergencyRevoke(address agent) external onlyOwner {
        uint256 tokenId = agentTokens[agent];
        if (tokenId != 0) {
            AgentProfile storage profile = agentProfiles[tokenId];
            profile.active = false;
            delete agentTokens[agent];
            _burn(tokenId);
            emit AgentTerminated(tokenId, agent);
            emit PermissionsBurned(tokenId);
        }
    }

    /**
     * @dev Token URI for metadata
     */
    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        require(_ownerOf(tokenId) != address(0), "Token does not exist");

        AgentProfile memory profile = agentProfiles[tokenId];
        string memory json = string(abi.encodePacked(
            '{"name": "SINC Agent Token #', tokenId.toString(), '",',
            '"description": "SINC Agent Ontology Token - Non-transferable access credential",',
            '"attributes": [',
            '{"trait_type": "Purpose", "value": "', profile.purpose, '"},',
            '{"trait_type": "Scope", "value": "', profile.scope.toString(), '"},',
            '{"trait_type": "Budget", "value": "', profile.budget.toString(), '"},',
            '{"trait_type": "Active", "value": "', profile.active ? 'true' : 'false', '"},',
            '{"trait_type": "Assigned Agent", "value": "', Strings.toHexString(uint256(uint160(profile.assignedAgent)), 20), '"}',
            ']}'
        ));

        return string(abi.encodePacked("data:application/json;base64,", base64Encode(bytes(json))));
    }

    /**
     * @dev Update agent reputation score (Scale: 0-1000)
     */
    function updateReputation(uint256 tokenId, uint256 newScore) external onlyOwner whenNotPaused nonReentrant {
        require(agentProfiles[tokenId].active, "Agent not active");
        require(newScore <= 1000, "Score exceeds maximum");
        
        agentReputation[tokenId] = newScore;
        emit ReputationUpdated(tokenId, newScore);
        emit MetadataUpdate(tokenId);
    }

    /**
     * @dev Apply reputation decay (Scale: 0-1000)
     */
    function decayReputation(uint256 tokenId) external onlyOwner whenNotPaused {
        uint256 currentRep = agentReputation[tokenId];
        if (currentRep > 0) {
            uint256 decayAmount = 5; // 0.5% decay per trigger
            agentReputation[tokenId] = currentRep > decayAmount ? currentRep - decayAmount : 0;
            emit ReputationUpdated(tokenId, agentReputation[tokenId]);
            emit MetadataUpdate(tokenId);
        }
    }

    /**
     * @dev Simple base64 encoding (for metadata)
     */
    function base64Encode(bytes memory data) internal pure returns (string memory) {
        if (data.length == 0) return "";

        string memory table = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        uint256 encodedLen = 4 * ((data.length + 2) / 3);
        string memory result = new string(encodedLen + 32);

        assembly {
            mstore(result, encodedLen)
            let dataPtr := add(data, 32)
            let endPtr := add(dataPtr, mload(data))
            let resultPtr := add(result, 32)

            for {} lt(dataPtr, endPtr) {} {
                let input := mload(dataPtr)
                mstore8(resultPtr, mload(add(table, and(shr(18, input), 63))))
                mstore8(add(resultPtr, 1), mload(add(table, and(shr(12, input), 63))))
                mstore8(add(resultPtr, 2), mload(add(table, and(shr(6, input), 63))))
                mstore8(add(resultPtr, 3), mload(add(table, and(input, 63))))
                resultPtr := add(resultPtr, 4)
                dataPtr := add(dataPtr, 3)
            }

            switch mod(mload(data), 3)
            case 1 {
                mstore8(sub(resultPtr, 1), 61)
                mstore8(sub(resultPtr, 2), 61)
            }
            case 2 {
                mstore8(sub(resultPtr, 1), 61)
            }
        }

        return result;
    }

    /**
     * @dev Delegate execution for a specific task
     * Allows logging of an agent's intent to perform an off-chain action
     */
    function delegateExecution(
        uint256 tokenId, 
        bytes32 taskHash, 
        string calldata metadata
    ) external whenNotPaused {
        require(agentProfiles[tokenId].active, "Agent not active");
        require(
            msg.sender == owner() || msg.sender == agentProfiles[tokenId].assignedAgent, 
            "Unauthorized"
        );
        
        emit ExecutionDelegated(tokenId, taskHash, metadata);
    }

    event ExecutionDelegated(uint256 indexed tokenId, bytes32 indexed taskHash, string metadata);
}