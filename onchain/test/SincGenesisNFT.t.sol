// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincGenesisNFT} from "../src/SincGenesisNFT.sol";

contract PassportGatedTestDAO {
    SincGenesisNFT public immutable passport;
    uint256 public immutable minReputation;
    bytes32 public immutable requiredSkill;
    mapping(address => bool) public canExecute;

    constructor(address passport_, uint256 minReputation_, bytes32 requiredSkill_) {
        passport = SincGenesisNFT(passport_);
        minReputation = minReputation_;
        requiredSkill = requiredSkill_;
    }

    function requestExecution() external {
        require(passport.hasPassport(msg.sender), "Passport required");
        require(passport.reputation(msg.sender) >= minReputation, "Low reputation");
        bytes32[] memory attestedSkills = passport.skills(msg.sender);
        bool hasRequired;
        for (uint256 i = 0; i < attestedSkills.length; i++) {
            if (attestedSkills[i] == requiredSkill) {
                hasRequired = true;
                break;
            }
        }
        require(hasRequired, "Skill missing");
        canExecute[msg.sender] = true;
    }
}

contract SincGenesisNFTTest is Test {
    SincGenesisNFT nft;
    PassportGatedTestDAO dao;
    address curve = makeAddr("curve");
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");
    bytes32 internal constant SKILL_TRADING = keccak256("SINCOR.SKILL.TRADING");

    function setUp() public {
        nft = new SincGenesisNFT(curve);
        dao = new PassportGatedTestDAO(address(nft), 10 ether, SKILL_TRADING);
    }

    function test_OnlyCurveCanMint() public {
        vm.prank(alice);
        vm.expectRevert("Only curve");
        nft.mint(alice, 1);
    }

    function test_CurveCanMint() public {
        vm.prank(curve);
        uint256 tokenId = nft.mint(alice, 1);
        assertEq(tokenId, 1);
        assertEq(nft.ownerOf(1), alice);
    }

    function test_TokenIdIncrements() public {
        vm.prank(curve);
        uint256 id1 = nft.mint(alice, 1);
        vm.prank(curve);
        uint256 id2 = nft.mint(bob, 2);
        assertEq(id1, 1);
        assertEq(id2, 2);
    }

    function test_TransferReverts_Soulbound() public {
        vm.prank(curve);
        nft.mint(alice, 1);
        vm.prank(alice);
        vm.expectRevert("Soulbound: non-transferable");
        nft.transferFrom(alice, bob, 1);
    }

    function test_SafeTransferReverts_Soulbound() public {
        vm.prank(curve);
        nft.mint(alice, 1);
        vm.prank(alice);
        vm.expectRevert("Soulbound: non-transferable");
        nft.safeTransferFrom(alice, bob, 1);
    }

    function test_MintEmitsEvent() public {
        vm.expectEmit(true, true, true, true);
        emit SincGenesisNFT.GenesisMinted(alice, 1, 42, block.timestamp);
        vm.prank(curve);
        nft.mint(alice, 42);
    }

    function test_PassportViewDataOnMint() public {
        vm.prank(curve);
        nft.mint(alice, 7);

        assertTrue(nft.hasPassport(alice));
        assertEq(nft.issuedAt(alice), uint64(block.timestamp));
        assertEq(nft.reputation(alice), 0);
        assertEq(nft.skills(alice).length, 0);
    }

    function test_OnlyCurveCanAttestPassport() public {
        vm.prank(curve);
        nft.mint(alice, 1);

        bytes32[] memory skillsToAdd = new bytes32[](1);
        skillsToAdd[0] = SKILL_TRADING;
        vm.prank(alice);
        vm.expectRevert("Only curve");
        nft.attestPassport(alice, 10 ether, skillsToAdd);
    }

    function test_AttestPassportUpdatesReputationAndSkills() public {
        vm.prank(curve);
        nft.mint(alice, 1);

        bytes32[] memory firstAttestation = new bytes32[](2);
        firstAttestation[0] = SKILL_TRADING;
        firstAttestation[1] = SKILL_TRADING;
        vm.prank(curve);
        nft.attestPassport(alice, 12 ether, firstAttestation);

        assertEq(nft.reputation(alice), 12 ether);
        bytes32[] memory attestedSkills = nft.skills(alice);
        assertEq(attestedSkills.length, 1);
        assertEq(attestedSkills[0], SKILL_TRADING);
        assertTrue(nft.hasSkillAttestation(alice, SKILL_TRADING));
    }

    function test_DAOAcceptsPassportForExecutionPermission() public {
        vm.prank(curve);
        nft.mint(alice, 1);

        bytes32[] memory skillsToAdd = new bytes32[](1);
        skillsToAdd[0] = SKILL_TRADING;
        vm.prank(curve);
        nft.attestPassport(alice, 11 ether, skillsToAdd);

        vm.prank(alice);
        dao.requestExecution();
        assertTrue(dao.canExecute(alice));
    }

    function test_DAORejectsWhenPassportMissing() public {
        vm.prank(bob);
        vm.expectRevert("Passport required");
        dao.requestExecution();
    }

    function test_DAORejectsWhenReputationTooLow() public {
        vm.prank(curve);
        nft.mint(alice, 1);

        bytes32[] memory skillsToAdd = new bytes32[](1);
        skillsToAdd[0] = SKILL_TRADING;
        vm.prank(curve);
        nft.attestPassport(alice, 9 ether, skillsToAdd);

        vm.prank(alice);
        vm.expectRevert("Low reputation");
        dao.requestExecution();
    }

    function test_DAORejectsWhenRequiredSkillMissing() public {
        vm.prank(curve);
        nft.mint(alice, 1);

        bytes32[] memory noSkills = new bytes32[](0);
        vm.prank(curve);
        nft.attestPassport(alice, 12 ether, noSkills);

        vm.prank(alice);
        vm.expectRevert("Skill missing");
        dao.requestExecution();
    }
}
