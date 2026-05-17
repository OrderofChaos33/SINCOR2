// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincBondingCurve} from "../src/SincBondingCurve.sol";
import {SincGenesisNFT} from "../src/SincGenesisNFT.sol";
import {MockSinc} from "./mocks/MockSinc.sol";

contract SincBondingCurveReferralTest is Test {
    SincBondingCurve curve;
    SincGenesisNFT nft;
    MockSinc sinc;
    address treasury = makeAddr("treasury");
    address alice = makeAddr("alice");
    address referrer = makeAddr("referrer");

    function setUp() public {
        sinc = new MockSinc(address(this));
        address predictedCurve = computeCreateAddress(address(this), vm.getNonce(address(this)) + 1);
        nft = new SincGenesisNFT(predictedCurve);
        curve = new SincBondingCurve(address(sinc), treasury, address(nft));
        sinc.transfer(address(curve), 65_000_000 * 10**8);
    }

    function test_ReferrerReceives3Percent() public {
        vm.deal(alice, 1 ether);
        uint256 referrerBefore = referrer.balance;
        vm.prank(alice);
        curve.buy{value: 1 ether}(1 ether, referrer);
        uint256 referrerAfter = referrer.balance;
        assertEq(referrerAfter - referrerBefore, 0.03 ether, "referrer did not receive 3%");
    }

    function test_NoReferrer_RoutesToTreasury() public {
        vm.deal(alice, 1 ether);
        uint256 treasuryBefore = treasury.balance;
        vm.prank(alice);
        curve.buy{value: 1 ether}(1 ether, address(0));
        uint256 treasuryAfter = treasury.balance;
        assertEq(treasuryAfter - treasuryBefore, 0.03 ether, "treasury did not receive 3% fallback");
    }

    function test_SelfReferral_RoutesToTreasury() public {
        vm.deal(alice, 1 ether);
        uint256 treasuryBefore = treasury.balance;
        vm.prank(alice);
        curve.buy{value: 1 ether}(1 ether, alice);  // alice refers herself
        uint256 treasuryAfter = treasury.balance;
        assertEq(treasuryAfter - treasuryBefore, 0.03 ether, "self-ref should fall back to treasury");
    }

    function test_CurveKeeps97PercentOfEth() public {
        vm.deal(alice, 1 ether);
        vm.prank(alice);
        curve.buy{value: 1 ether}(1 ether, referrer);
        // ethAccumulated should be 0.97 ether (3% went to referrer, not curve)
        assertEq(curve.ethAccumulated(), 0.97 ether, "curve should keep 97%");
    }
}
