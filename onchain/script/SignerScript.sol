// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import "forge-std/Script.sol";

abstract contract SignerScript is Script {
    error NoSignerKey();

    function _loadSignerKey() internal view returns (uint256) {
        try vm.envUint("SAFE_PRIVATE_KEY") returns (uint256 k) {
            return k;
        } catch {}

        try vm.envUint("TREASURY_PRIVATE_KEY") returns (uint256 k) {
            return k;
        } catch {}

        try vm.envUint("DEPLOYER_PRIVATE_KEY") returns (uint256 k) {
            return k;
        } catch {}

        try vm.envUint("CREATOR_PRIVATE_KEY") returns (uint256 k) {
            return k;
        } catch {}

        revert NoSignerKey();
    }
}