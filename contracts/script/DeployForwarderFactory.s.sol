// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {ForwarderFactory} from "../src/ForwarderFactory.sol";

/// @notice Deploys one {ForwarderFactory} for a given founder wallet.
///
/// Usage:
///   forge script script/DeployForwarderFactory.s.sol \
///     --rpc-url <network> --broadcast --verify
///
/// Required env vars:
///   PRIVATE_KEY     deployer key that pays for and signs the deployment
///   FOUNDER_WALLET  the fixed destination every deposit address forwards to
contract DeployForwarderFactory is Script {
    function run() external returns (ForwarderFactory factory) {
        uint256 deployerKey = vm.envUint("PRIVATE_KEY");
        address founderWallet = vm.envAddress("FOUNDER_WALLET");

        vm.startBroadcast(deployerKey);
        factory = new ForwarderFactory(founderWallet);
        vm.stopBroadcast();

        console.log("ForwarderFactory deployed at:", address(factory));
        console.log("implementation:", factory.implementation());
        console.log("founderWallet: ", factory.founderWallet());
    }
}
