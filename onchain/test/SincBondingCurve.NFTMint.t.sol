// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincBondingCurve} from "../src/SincBondingCurve.sol";
import {SincGenesisNFT} from "../src/SincGenesisNFT.sol";
import {MockSinc} from "./mocks/MockSinc.sol";

contract SincBondingCurveNFTMintTest is Test {
    SincBondingCurve curve;
    SincGenesisNFT nft;
    MockSinc sinc;
    address treasury = makeAddr("treasury");
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");

    function setUp() public {
        sinc = new MockSinc(address(this));
        address predictedCurve = computeCreateAddress(address(this), vm.getNonce(address(this)) + 1);
        nft = new SincGenesisNFT(predictedCurve);
        curve = new SincBondingCurve(address(sinc), treasury, address(nft));
        sinc.transfer(address(curve), 65_000_000 * 10**8);
    }

    function test_FirstBuy_MintsNFT() public {
        vm.deal(alice, 0.01 ether);
        vm.prank(alice);
        curve.buy{value: 0.01 ether}(0.01 ether, address(0));
        assertEq(nft.balanceOf(alice), 1, "alice should have 1 Genesis NFT after first buy");
        assertEq(nft.ownerOf(1), alice);
    }

    function test_SecondBuy_DoesNotMintAgain() public {
        vm.deal(alice, 0.02 ether);
        vm.prank(alice);
        curve.buy{value: 0.01 ether}(0.01 ether, address(0));
        vm.prank(alice);
        curve.buy{value: 0.01 ether}(0.01 ether, address(0));
        assertEq(nft.balanceOf(alice), 1, "alice should still have only 1 NFT after second buy");
    }

    function test_DifferentBuyers_GetDifferentNFTs() public {
        vm.deal(alice, 0.01 ether);
        vm.deal(bob, 0.01 ether);
        vm.prank(alice);
        curve.buy{value: 0.01 ether}(0.01 ether, address(0));
        vm.prank(bob);
        curve.buy{value: 0.01 ether}(0.01 ether, address(0));
        assertEq(nft.ownerOf(1), alice);
        assertEq(nft.ownerOf(2), bob);
    }

    function test_BuyOrderNumberInEvent() public {
        vm.deal(alice, 0.01 ether);
        vm.expectEmit(true, true, true, false);
        emit SincGenesisNFT.GenesisMinted(alice, 1, 1, block.timestamp);
        vm.prank(alice);
        curve.buy{value: 0.01 ether}(0.01 ether, address(0));
    }
}
