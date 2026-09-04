// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

import {ISincVaultFactory} from "../interfaces/ISincVaultSystem.sol";
import {SincConstants} from "./SincConstants.sol";
import {Sinc4626Vault} from "./Sinc4626Vault.sol";
import {SincStrategyRouter} from "./SincStrategyRouter.sol";
import {SincFeeFlywheel} from "./SincFeeFlywheel.sol";
import {SincPSM} from "./SincPSM.sol";
import {SincRiskModule} from "./SincRiskModule.sol";

contract SincVaultFactory is ISincVaultFactory, AccessControl {
    bytes32 public constant DEPLOYER_ROLE = keccak256("DEPLOYER_ROLE");

    struct VaultDeployConfig {
        address admin;
        address aeroToken;
        address aeroAdapter;
        address assetPriceFeed;
        address aeroPriceFeed;
        address scPriceFeed;
        address targetPriceFeed;
        address gaugePrimary;
        address gaugeSecondary;
        uint16 performanceFeeBps;
        uint16 idleBufferBps;
        uint16 maxOracleDeviationBps;
        uint16 maxStrategyCount;
        uint32 oracleHeartbeat;
        uint256 lockDuration;
        uint256 psmBand0;
        uint256 psmBand1;
    }

    struct VaultStack {
        address vault;
        address router;
        address flywheel;
        address psm;
        address riskModule;
    }

    mapping(address asset => VaultStack) public stackByAsset;

    error InvalidConfig();
    error VaultExists();

    constructor(address admin) {
        if (admin == address(0)) revert InvalidConfig();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(DEPLOYER_ROLE, admin);
    }

    function deployVault(address asset, string calldata name, string calldata symbol, bytes calldata config)
        external
        onlyRole(DEPLOYER_ROLE)
        returns (address vault)
    {
        if (asset == address(0)) revert InvalidConfig();
        if (stackByAsset[asset].vault != address(0)) revert VaultExists();

        VaultDeployConfig memory cfg = abi.decode(config, (VaultDeployConfig));
        if (
            cfg.admin == address(0) || cfg.aeroToken == address(0) || cfg.aeroAdapter == address(0)
                || cfg.assetPriceFeed == address(0) || cfg.aeroPriceFeed == address(0) || cfg.scPriceFeed == address(0)
                || cfg.targetPriceFeed == address(0) || cfg.performanceFeeBps > SincConstants.BPS_DENOM
                || cfg.idleBufferBps > SincConstants.BPS_DENOM || cfg.maxStrategyCount == 0 || cfg.oracleHeartbeat == 0
        ) revert InvalidConfig();

        SincRiskModule risk = new SincRiskModule(cfg.admin, cfg.maxOracleDeviationBps);
        risk.setOracle(asset, cfg.assetPriceFeed, cfg.oracleHeartbeat);
        risk.setOracle(cfg.aeroToken, cfg.aeroPriceFeed, cfg.oracleHeartbeat);

        SincStrategyRouter router = new SincStrategyRouter(asset, cfg.admin, cfg.maxStrategyCount);

        SincFeeFlywheel flywheel = new SincFeeFlywheel(
            cfg.admin,
            cfg.aeroToken,
            cfg.aeroAdapter,
            address(risk),
            SincConstants.TREASURY_SEED_ADDRESS,
            cfg.maxOracleDeviationBps,
            cfg.lockDuration
        );

        flywheel.setGaugeWhitelist(cfg.gaugePrimary, true);
        if (cfg.gaugeSecondary != address(0)) {
            flywheel.setGaugeWhitelist(cfg.gaugeSecondary, true);
            flywheel.setGaugeWeight(cfg.gaugeSecondary, 5_000);
        }
        flywheel.setGaugeWeight(cfg.gaugePrimary, 5_000);

        Sinc4626Vault deployedVault = new Sinc4626Vault(
            asset,
            name,
            symbol,
            cfg.admin,
            address(router),
            address(flywheel),
            cfg.idleBufferBps,
            cfg.performanceFeeBps,
            cfg.gaugePrimary
        );

        router.setVault(address(deployedVault));
        flywheel.grantRole(flywheel.KEEPER_ROLE(), address(deployedVault));

        SincPSM psm = new SincPSM(
            cfg.admin,
            address(deployedVault),
            SincConstants.TREASURY_SEED_ADDRESS,
            cfg.scPriceFeed,
            cfg.targetPriceFeed,
            cfg.psmBand0,
            cfg.psmBand1
        );

        stackByAsset[asset] = VaultStack({
            vault: address(deployedVault),
            router: address(router),
            flywheel: address(flywheel),
            psm: address(psm),
            riskModule: address(risk)
        });

        vault = address(deployedVault);
        emit VaultDeployed(asset, vault, address(router), address(flywheel), address(psm), address(risk));
    }
}
