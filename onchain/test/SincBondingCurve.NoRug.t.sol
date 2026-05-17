// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincBondingCurve} from "../src/SincBondingCurve.sol";
import {SincGenesisNFT} from "../src/SincGenesisNFT.sol";
import {MockSinc} from "./mocks/MockSinc.sol";

contract SincBondingCurveNoRugTest is Test {
    SincBondingCurve curve;

    function setUp() public {
        MockSinc sinc = new MockSinc(address(this));
        address predictedCurve = computeCreateAddress(address(this), vm.getNonce(address(this)) + 1);
        SincGenesisNFT nft = new SincGenesisNFT(predictedCurve);
        curve = new SincBondingCurve(address(sinc), address(this), address(nft), address(0x1111), address(0x2222));
    }

    function test_NoWithdrawEthFunction() public {
        (bool ok,) = address(curve).call(abi.encodeWithSignature("withdrawETH(uint256)", 1 ether));
        assertFalse(ok, "withdrawETH should not exist");
    }

    function test_NoWithdrawTokensFunction() public {
        (bool ok,) = address(curve).call(abi.encodeWithSignature("withdrawTokens(uint256)", 1));
        assertFalse(ok, "withdrawTokens should not exist");
    }

    function test_NoEmergencyFunction() public {
        (bool ok,) = address(curve).call(abi.encodeWithSignature("emergencyWithdraw()"));
        assertFalse(ok, "emergencyWithdraw should not exist");
    }

    function test_NoOwnerFunction() public {
        (bool ok,) = address(curve).call(abi.encodeWithSignature("owner()"));
        assertFalse(ok, "owner() should not exist");
    }

    function test_NoTransferOwnership() public {
        (bool ok,) = address(curve).call(abi.encodeWithSignature("transferOwnership(address)", address(1)));
        assertFalse(ok, "transferOwnership should not exist");
    }

    function test_NoPauseFunction() public {
        (bool ok,) = address(curve).call(abi.encodeWithSignature("pause()"));
        assertFalse(ok, "pause should not exist");
    }
}
