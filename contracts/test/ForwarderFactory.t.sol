// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ForwarderFactory} from "../src/ForwarderFactory.sol";
import {Forwarder} from "../src/Forwarder.sol";
import {MockERC20} from "./mocks/MockERC20.sol";

contract ForwarderFactoryTest is Test {
    address founderWallet = makeAddr("founderWallet");
    address payer = makeAddr("payer");
    address relayer = makeAddr("relayer");

    ForwarderFactory factory;
    MockERC20 token;

    function setUp() public {
        factory = new ForwarderFactory(founderWallet);
        token = new MockERC20();
    }

    function _emptyTokens() internal pure returns (address[] memory tokens) {
        tokens = new address[](0);
    }

    function _singleToken(address t) internal pure returns (address[] memory tokens) {
        tokens = new address[](1);
        tokens[0] = t;
    }

    function test_ComputeAddressMatchesDeployedCloneAddress() public {
        bytes32 salt = keccak256("invoice-1");
        address predicted = factory.computeAddress(salt);

        assertEq(predicted.code.length, 0, "should be undeployed before first sweep");

        vm.prank(relayer);
        address clone = factory.deployAndSweep(salt, _emptyTokens());

        assertEq(clone, predicted);
        assertGt(clone.code.length, 0);
    }

    function test_DifferentSaltsProduceDifferentAddresses() public view {
        address a = factory.computeAddress(keccak256("invoice-a"));
        address b = factory.computeAddress(keccak256("invoice-b"));
        assertTrue(a != b);
    }

    function test_DeployAndSweepForwardsNativeBalance() public {
        bytes32 salt = keccak256("invoice-native");
        address depositAddress = factory.computeAddress(salt);

        vm.deal(payer, 1 ether);
        vm.prank(payer);
        (bool ok,) = depositAddress.call{value: 1 ether}("");
        assertTrue(ok);
        assertEq(depositAddress.balance, 1 ether);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens());

        assertEq(depositAddress.balance, 0);
        assertEq(founderWallet.balance, 1 ether);
    }

    function test_DeployAndSweepForwardsErc20Balance() public {
        bytes32 salt = keccak256("invoice-erc20");
        address depositAddress = factory.computeAddress(salt);

        token.mint(depositAddress, 500 ether);
        assertEq(token.balanceOf(depositAddress), 500 ether);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _singleToken(address(token)));

        assertEq(token.balanceOf(depositAddress), 0);
        assertEq(token.balanceOf(founderWallet), 500 ether);
    }

    function test_ReceiveFundsBeforeDeploymentThenSweepForwardsAll() public {
        bytes32 salt = keccak256("invoice-counterfactual");
        address depositAddress = factory.computeAddress(salt);
        assertEq(depositAddress.code.length, 0);

        vm.deal(payer, 2 ether);
        vm.prank(payer);
        (bool ok,) = depositAddress.call{value: 2 ether}("");
        assertTrue(ok);
        token.mint(depositAddress, 100 ether);

        assertEq(depositAddress.code.length, 0, "still undeployed after receiving funds");

        vm.prank(relayer);
        factory.deployAndSweep(salt, _singleToken(address(token)));

        assertGt(depositAddress.code.length, 0, "deployed as part of the sweep");
        assertEq(founderWallet.balance, 2 ether);
        assertEq(token.balanceOf(founderWallet), 100 ether);
    }

    function test_SecondSweepAfterTopUpForwardsOnlyTheNewAmount() public {
        bytes32 salt = keccak256("invoice-topup");
        address depositAddress = factory.computeAddress(salt);

        vm.deal(payer, 3 ether);
        vm.prank(payer);
        (bool ok1,) = depositAddress.call{value: 1 ether}("");
        assertTrue(ok1);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens());
        assertEq(founderWallet.balance, 1 ether);

        vm.prank(payer);
        (bool ok2,) = depositAddress.call{value: 2 ether}("");
        assertTrue(ok2);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens());
        assertEq(founderWallet.balance, 3 ether);
    }

    function test_SweepWithNoBalanceIsANoOp() public {
        bytes32 salt = keccak256("invoice-empty");

        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens());

        assertEq(founderWallet.balance, 0);
    }

    function test_DeployAndSweepIsPermissionless(address caller) public {
        vm.assume(caller != address(0));
        bytes32 salt = keccak256("invoice-permissionless");
        address depositAddress = factory.computeAddress(salt);

        vm.deal(payer, 1 ether);
        vm.prank(payer);
        (bool ok,) = depositAddress.call{value: 1 ether}("");
        assertTrue(ok);

        vm.prank(caller);
        factory.deployAndSweep(salt, _emptyTokens());

        assertEq(founderWallet.balance, 1 ether);
    }

    function test_AllClonesShareTheSameImmutableFounderWallet() public {
        address cloneA = factory.deployAndSweep(keccak256("invoice-a"), _emptyTokens());
        address cloneB = factory.deployAndSweep(keccak256("invoice-b"), _emptyTokens());

        assertEq(Forwarder(payable(cloneA)).FOUNDER_WALLET(), founderWallet);
        assertEq(Forwarder(payable(cloneB)).FOUNDER_WALLET(), founderWallet);
    }

    function test_ConstructorRevertsOnZeroFounderWallet() public {
        vm.expectRevert("Forwarder: zero founder wallet");
        new ForwarderFactory(address(0));
    }
}
