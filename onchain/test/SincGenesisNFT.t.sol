// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincGenesisNFT} from "../src/SincGenesisNFT.sol";

contract SincGenesisNFTTest is Test {
    SincGenesisNFT nft;
    address curve = makeAddr("curve");
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");

    function setUp() public {
        nft = new SincGenesisNFT(curve);
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
}
