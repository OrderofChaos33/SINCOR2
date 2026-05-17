// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincBondingCurve} from "../src/SincBondingCurve.sol";
import {SincGenesisNFT} from "../src/SincGenesisNFT.sol";
import {MockSinc} from "./mocks/MockSinc.sol";

contract SincBondingCurveMathTest is Test {
    SincBondingCurve curve;
    SincGenesisNFT nft;
    MockSinc sinc;
    address treasury = makeAddr("treasury");
    address alice = makeAddr("alice");

    uint256 constant CURVE_SUPPLY = 65_000_000 * 10**8;

    function setUp() public {
        sinc = new MockSinc(address(this));
        // We construct NFT with a placeholder, then replace via a fresh deploy after curve exists.
        // Simpler: predict curve address with CREATE2, but for tests use a 2-step deploy.
        address predictedCurve = computeCreateAddress(address(this), vm.getNonce(address(this)) + 1);
        nft = new SincGenesisNFT(predictedCurve);
        curve = new SincBondingCurve(address(sinc), treasury, address(nft), address(0x1111), address(0x2222));
        require(address(curve) == predictedCurve, "address prediction failed");
        sinc.transfer(address(curve), CURVE_SUPPLY);
    }

    function test_InitialPrice_IsLow() public view {
        // Initial price should be in the $0.0001 range; in wei terms,
        // 1 SINC (10^8 atomic units) should cost approximately 10^11 wei (= 0.0000001 ETH)
        uint256 cost = curve.getBuyCost(10**8);  // 1 SINC
        assertLt(cost, 10**13, "initial price > 0.00001 ETH per SINC -- too high");
        assertGt(cost, 10**10, "initial price < 0.0000001 ETH per SINC -- too low");
    }

    function test_BuyIncreasesPrice() public {
        uint256 priceBefore = curve.getBuyCost(10**8);
        vm.deal(alice, 1 ether);
        vm.prank(alice);
        curve.buy{value: 1 ether}(1 ether, address(0));
        uint256 priceAfter = curve.getBuyCost(10**8);
        assertGt(priceAfter, priceBefore, "price did not increase after buy");
    }

    function test_BuyAndSell_RoundTrip_Loses() public {
        // Selling immediately after buying must lose value (the curve's spread)
        vm.deal(alice, 1 ether);
        vm.prank(alice);
        uint256 sincReceived = curve.buy{value: 1 ether}(1 ether, address(0));

        vm.prank(alice);
        sinc.approve(address(curve), sincReceived);
        vm.prank(alice);
        uint256 ethReturned = curve.sell(sincReceived);

        assertLt(ethReturned, 1 ether, "round trip did not lose value");
    }

    function test_CannotBuyMoreThanCurveSupply() public {
        vm.deal(alice, 1000 ether);
        vm.prank(alice);
        vm.expectRevert("Insufficient SINC in curve");
        curve.buy{value: 1000 ether}(1000 ether, address(0));
    }

    function test_CannotSellMoreThanSold() public {
        vm.deal(alice, 0.001 ether);
        vm.prank(alice);
        uint256 received = curve.buy{value: 0.001 ether}(0.001 ether, address(0));

        sinc.approve(address(curve), received * 2);
        vm.expectRevert("Sell exceeds amount sold");
        curve.sell(received * 2);
    }
}
