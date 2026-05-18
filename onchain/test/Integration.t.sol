// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Test.sol";
import {SincBondingCurve} from "../src/SincBondingCurve.sol";
import {SincGenesisNFT} from "../src/SincGenesisNFT.sol";
import {SincLimitOrderHook} from "../src/SincLimitOrderHook.sol";
import {MockSinc} from "./mocks/MockSinc.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";

contract IntegrationTest is Test {
    address constant POOL_MANAGER = 0x498581fF718922c3f8e6A244956aF099B2652b2b;
    address constant POSITION_MANAGER = 0x7C5f5A4bBd8fD63184577525326123B519429bDc;

    SincBondingCurve curve;
    SincGenesisNFT nft;
    SincLimitOrderHook hook;
    MockSinc sinc;
    address treasury = makeAddr("treasury");
    address alice = makeAddr("alice");

    function setUp() public {
        vm.createSelectFork(vm.rpcUrl("base"));

        sinc = new MockSinc(address(this));
        address predictedCurve = computeCreateAddress(address(this), vm.getNonce(address(this)) + 1);
        nft = new SincGenesisNFT(predictedCurve);
        curve = new SincBondingCurve(address(sinc), treasury, address(nft), POOL_MANAGER, POSITION_MANAGER);

        uint160 required = uint160(Hooks.AFTER_INITIALIZE_FLAG | Hooks.BEFORE_SWAP_FLAG | Hooks.AFTER_SWAP_FLAG);
        bytes memory creationCode = abi.encodePacked(type(SincLimitOrderHook).creationCode, abi.encode(POOL_MANAGER));
        bytes32 codeHash = keccak256(creationCode);
        bytes32 salt;
        address predicted;
        for (uint256 i = 0; i < 1000000; i++) {
            salt = bytes32(i);
            predicted = address(uint160(uint256(keccak256(abi.encodePacked(
                bytes1(0xff), address(this), salt, codeHash
            )))));
            if (uint160(predicted) & 0x3FFF == required) break;
        }
        require(uint160(predicted) & 0x3FFF == required, "salt not found");
        address h;
        assembly { h := create2(0, add(creationCode, 0x20), mload(creationCode), salt) }
        hook = SincLimitOrderHook(payable(h));
        curve.setHook(h);

        sinc.transfer(address(curve), 65_000_000 * 10**8);
    }

    function test_FullPath_BuyGraduateSwap() public {
        vm.deal(alice, 1 ether);
        vm.prank(alice);
        uint256 sincOut = curve.buy{value: 0.6 ether}(0.6 ether, address(0));
        assertGt(sincOut, 0, "alice should receive SINC");
        assertEq(nft.balanceOf(alice), 1, "alice should have Genesis NFT");

        curve.graduate();
        assertTrue(curve.graduated());
    }
}
