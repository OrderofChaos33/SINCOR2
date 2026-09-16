// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";

contract SincGenesisNFT is ERC721 {
    address public immutable curve;
    uint256 public nextTokenId = 1;
    mapping(address => uint256) public reputationScore;
    mapping(address => uint64) public passportIssuedAt;
    mapping(address => bytes32[]) private _attestedSkills;
    mapping(address => mapping(bytes32 => bool)) public hasSkillAttestation;

    event GenesisMinted(
        address indexed holder,
        uint256 indexed tokenId,
        uint256 indexed buyOrderNumber,
        uint256 timestamp
    );
    event PassportAttested(
        address indexed holder,
        uint256 reputationScore,
        bytes32[] addedSkills,
        uint256 timestamp
    );

    constructor(address _curve) ERC721("SINC Genesis Holder", "SINC-GEN") {
        curve = _curve;
    }

    function mint(address to, uint256 buyOrderNumber) external returns (uint256 tokenId) {
        require(msg.sender == curve, "Only curve");
        tokenId = nextTokenId++;
        _safeMint(to, tokenId);
        if (passportIssuedAt[to] == 0) {
            passportIssuedAt[to] = uint64(block.timestamp);
        }
        emit GenesisMinted(to, tokenId, buyOrderNumber, block.timestamp);
    }

    function hasPassport(address holder) external view returns (bool) {
        return balanceOf(holder) > 0;
    }

    function reputation(address holder) external view returns (uint256) {
        return reputationScore[holder];
    }

    function issuedAt(address holder) external view returns (uint64) {
        return passportIssuedAt[holder];
    }

    function skills(address holder) external view returns (bytes32[] memory) {
        return _attestedSkills[holder];
    }

    function attestPassport(address holder, uint256 newScore, bytes32[] calldata skillsToAdd) external {
        require(msg.sender == curve, "Only curve");
        require(balanceOf(holder) > 0, "Passport missing");
        reputationScore[holder] = newScore;

        uint256 len = skillsToAdd.length;
        bytes32[] memory addedSkills = new bytes32[](len);
        uint256 addedCount;
        for (uint256 i = 0; i < len; i++) {
            bytes32 skill = skillsToAdd[i];
            if (skill == bytes32(0) || hasSkillAttestation[holder][skill]) {
                continue;
            }
            hasSkillAttestation[holder][skill] = true;
            _attestedSkills[holder].push(skill);
            addedSkills[addedCount] = skill;
            addedCount++;
        }

        bytes32[] memory finalAddedSkills = new bytes32[](addedCount);
        for (uint256 j = 0; j < addedCount; j++) {
            finalAddedSkills[j] = addedSkills[j];
        }

        emit PassportAttested(holder, newScore, finalAddedSkills, block.timestamp);
    }

    function _update(address to, uint256 tokenId, address auth)
        internal override returns (address)
    {
        address from = _ownerOf(tokenId);
        require(from == address(0) || to == address(0), "Soulbound: non-transferable");
        return super._update(to, tokenId, auth);
    }
}
