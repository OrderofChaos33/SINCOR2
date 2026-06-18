// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";

interface IFjordFixedPricePool {
    function close() external;
    function canClose() external view returns (bool);
    function saleEnd() external view returns (uint256);
    function owner() external view returns (address);
}

/// @notice Owner `close()` on Fjord MultiModal FixedPricePool clones after sale ends.
/// @dev Do NOT call before `canClose()` is true (saleEnd or sold out). Signer = treasury owner.
contract CloseFjordFixedPricePools is Script {
    address constant TREASURY = 0xAf9B539D8043C634b7E611818518BA7E850F289e;

    address[3] internal pools = [
        0xff38C22C5932Cf4283F33A892763FCCDe2EEa6aD,
        0xa497DB488e5d6aCCE3CaB5fBe19cB5C63de91959,
        0x2feDA1347981dCBdcbd249E28B0f5897A5043CCC
    ];

    function run() external {
        uint256 signerKey = vm.envUint("TREASURY_PRIVATE_KEY");
        address signer = vm.addr(signerKey);

        for (uint256 i = 0; i < pools.length; i++) {
            address pool = pools[i];
            address owner = IFjordFixedPricePool(pool).owner();
            uint256 end = IFjordFixedPricePool(pool).saleEnd();
            bool closable = IFjordFixedPricePool(pool).canClose();

            console.log("Pool", i, pool);
            console.log("  owner:", owner);
            console.log("  saleEnd:", end);
            console.log("  canClose:", closable);
            require(owner == signer, "signer is not pool owner");
            require(closable, "sale still active - wait until saleEnd");
        }

        vm.startBroadcast(signerKey);
        for (uint256 i = 0; i < pools.length; i++) {
            IFjordFixedPricePool(pools[i]).close();
            console.log("close() sent for pool", i);
        }
        vm.stopBroadcast();
    }
}