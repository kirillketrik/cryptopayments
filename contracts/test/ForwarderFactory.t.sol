// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {ForwarderFactory} from "../src/ForwarderFactory.sol";
import {Forwarder} from "../src/Forwarder.sol";
import {MockERC20} from "./mocks/MockERC20.sol";
import {ReentrantFeeRecipient} from "./mocks/ReentrantFeeRecipient.sol";

contract ForwarderFactoryTest is Test {
    uint256 constant MAX_SWEEP_FEE = 0.001 ether;

    address founderWallet = makeAddr("founderWallet");
    address payer = makeAddr("payer");
    address relayer = makeAddr("relayer");

    ForwarderFactory factory;
    MockERC20 token;

    function setUp() public {
        factory = new ForwarderFactory(founderWallet, MAX_SWEEP_FEE);
        token = new MockERC20();
    }

    function _emptyTokens() internal pure returns (address[] memory tokens) {
        tokens = new address[](0);
    }

    function _singleToken(address t) internal pure returns (address[] memory tokens) {
        tokens = new address[](1);
        tokens[0] = t;
    }

    function _payNative(address depositAddress, uint256 amount) internal {
        vm.deal(payer, payer.balance + amount);
        vm.prank(payer);
        (bool ok,) = depositAddress.call{value: amount}("");
        assertTrue(ok);
    }

    function test_ComputeAddressMatchesDeployedCloneAddress() public {
        bytes32 salt = keccak256("invoice-1");
        address predicted = factory.computeAddress(salt);

        assertEq(predicted.code.length, 0, "should be undeployed before first sweep");

        vm.prank(relayer);
        address clone = factory.deployAndSweep(salt, _emptyTokens(), 0);

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
        _payNative(depositAddress, 1 ether);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens(), 0);

        assertEq(depositAddress.balance, 0);
        assertEq(founderWallet.balance, 1 ether);
    }

    function test_DeployAndSweepForwardsErc20Balance() public {
        bytes32 salt = keccak256("invoice-erc20");
        address depositAddress = factory.computeAddress(salt);

        token.mint(depositAddress, 500 ether);
        assertEq(token.balanceOf(depositAddress), 500 ether);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _singleToken(address(token)), 0);

        assertEq(token.balanceOf(depositAddress), 0);
        assertEq(token.balanceOf(founderWallet), 500 ether);
    }

    function test_ReceiveFundsBeforeDeploymentThenSweepForwardsAll() public {
        bytes32 salt = keccak256("invoice-counterfactual");
        address depositAddress = factory.computeAddress(salt);
        assertEq(depositAddress.code.length, 0);

        _payNative(depositAddress, 2 ether);
        token.mint(depositAddress, 100 ether);

        assertEq(depositAddress.code.length, 0, "still undeployed after receiving funds");

        vm.prank(relayer);
        factory.deployAndSweep(salt, _singleToken(address(token)), 0);

        assertGt(depositAddress.code.length, 0, "deployed as part of the sweep");
        assertEq(founderWallet.balance, 2 ether);
        assertEq(token.balanceOf(founderWallet), 100 ether);
    }

    function test_SecondSweepAfterTopUpForwardsOnlyTheNewAmount() public {
        bytes32 salt = keccak256("invoice-topup");
        address depositAddress = factory.computeAddress(salt);

        _payNative(depositAddress, 1 ether);
        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens(), 0);
        assertEq(founderWallet.balance, 1 ether);

        _payNative(depositAddress, 2 ether);
        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens(), 0);
        assertEq(founderWallet.balance, 3 ether);
    }

    function test_SweepWithNoBalanceIsANoOp() public {
        bytes32 salt = keccak256("invoice-empty");

        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens(), 0);

        assertEq(founderWallet.balance, 0);
    }

    function test_DeployAndSweepIsPermissionless(address caller) public {
        vm.assume(caller != address(0));
        bytes32 salt = keccak256("invoice-permissionless");
        address depositAddress = factory.computeAddress(salt);
        _payNative(depositAddress, 1 ether);

        vm.prank(caller);
        factory.deployAndSweep(salt, _emptyTokens(), 0);

        assertEq(founderWallet.balance, 1 ether);
    }

    function test_AllClonesShareTheSameImmutableFounderWallet() public {
        address cloneA = factory.deployAndSweep(keccak256("invoice-a"), _emptyTokens(), 0);
        address cloneB = factory.deployAndSweep(keccak256("invoice-b"), _emptyTokens(), 0);

        assertEq(Forwarder(payable(cloneA)).FOUNDER_WALLET(), founderWallet);
        assertEq(Forwarder(payable(cloneB)).FOUNDER_WALLET(), founderWallet);
    }

    function test_ConstructorRevertsOnZeroFounderWallet() public {
        vm.expectRevert("Forwarder: zero founder wallet");
        new ForwarderFactory(address(0), MAX_SWEEP_FEE);
    }

    // -- Sweep fee ------------------------------------------------------

    function test_SweepFeePaysCallerAndReducesFounderAmount() public {
        bytes32 salt = keccak256("invoice-fee");
        address depositAddress = factory.computeAddress(salt);
        _payNative(depositAddress, 1 ether);

        uint256 fee = 0.0005 ether;
        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens(), fee);

        assertEq(relayer.balance, fee);
        assertEq(founderWallet.balance, 1 ether - fee);
    }

    function test_SweepFeeAtExactlyMaxSucceeds() public {
        bytes32 salt = keccak256("invoice-fee-max");
        address depositAddress = factory.computeAddress(salt);
        _payNative(depositAddress, 1 ether);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens(), MAX_SWEEP_FEE);

        assertEq(relayer.balance, MAX_SWEEP_FEE);
        assertEq(founderWallet.balance, 1 ether - MAX_SWEEP_FEE);
    }

    function test_SweepFeeAboveMaxIsClampedToMax() public {
        bytes32 salt = keccak256("invoice-fee-too-high");
        address depositAddress = factory.computeAddress(salt);
        _payNative(depositAddress, 1 ether);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens(), MAX_SWEEP_FEE + 1);

        assertEq(relayer.balance, MAX_SWEEP_FEE, "fee clamped to the immutable cap");
        assertEq(founderWallet.balance, 1 ether - MAX_SWEEP_FEE);
    }

    function test_SweepFeeAboveNativeBalanceIsClampedToBalance() public {
        bytes32 salt = keccak256("invoice-fee-underfunded");
        address depositAddress = factory.computeAddress(salt);
        _payNative(depositAddress, 0.0001 ether);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens(), 0.0005 ether);

        assertEq(relayer.balance, 0.0001 ether, "fee clamped to whatever the clone actually held");
        assertEq(founderWallet.balance, 0);
    }

    function test_TokenOnlyDepositSweepsWithZeroFee() public {
        bytes32 salt = keccak256("invoice-token-only");
        address depositAddress = factory.computeAddress(salt);
        token.mint(depositAddress, 50 ether);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _singleToken(address(token)), 0);

        assertEq(token.balanceOf(founderWallet), 50 ether);
        assertEq(relayer.balance, 0);
    }

    function test_TokenOnlyDepositClampsFeeToZeroInsteadOfReverting() public {
        bytes32 salt = keccak256("invoice-token-only-fee");
        address depositAddress = factory.computeAddress(salt);
        token.mint(depositAddress, 50 ether);

        vm.prank(relayer);
        factory.deployAndSweep(salt, _singleToken(address(token)), 0.0001 ether);

        assertEq(relayer.balance, 0, "no native balance to pay a fee from");
        assertEq(token.balanceOf(founderWallet), 50 ether, "tokens still forwarded in full");
    }

    function test_TwoRelayersRacingToSweepTheSameInvoiceBothSucceed() public {
        bytes32 salt = keccak256("invoice-race");
        address depositAddress = factory.computeAddress(salt);
        _payNative(depositAddress, 1 ether);

        address secondRelayer = makeAddr("secondRelayer");

        // Both relayers independently query the balance, see 1 ether, and
        // each optimistically request the full MAX_SWEEP_FEE. Whichever
        // transaction lands first gets it in full; the second no longer
        // reverts (and waste its gas for nothing) just because the balance
        // it actually sees by the time it lands is smaller — it's clamped
        // instead, and the sweep still goes through.
        vm.prank(relayer);
        factory.deployAndSweep(salt, _emptyTokens(), MAX_SWEEP_FEE);
        assertEq(relayer.balance, MAX_SWEEP_FEE);

        _payNative(depositAddress, MAX_SWEEP_FEE / 2);
        vm.prank(secondRelayer);
        factory.deployAndSweep(salt, _emptyTokens(), MAX_SWEEP_FEE);
        assertEq(secondRelayer.balance, MAX_SWEEP_FEE / 2, "clamped to what was actually available");

        assertEq(founderWallet.balance, 1 ether - MAX_SWEEP_FEE);
    }

    // -- Batch sweep ------------------------------------------------------

    function test_BatchDeployAndSweepSweepsMultipleInvoicesInOneCall() public {
        bytes32[] memory salts = new bytes32[](2);
        salts[0] = keccak256("invoice-batch-a");
        salts[1] = keccak256("invoice-batch-b");

        address depositA = factory.computeAddress(salts[0]);
        address depositB = factory.computeAddress(salts[1]);
        _payNative(depositA, 1 ether);
        _payNative(depositB, 2 ether);

        address[][] memory tokensPerSalt = new address[][](2);
        tokensPerSalt[0] = _emptyTokens();
        tokensPerSalt[1] = _emptyTokens();

        uint256[] memory fees = new uint256[](2);
        fees[0] = 0.0001 ether;
        fees[1] = 0.0002 ether;

        vm.prank(relayer);
        address[] memory clones = factory.batchDeployAndSweep(salts, tokensPerSalt, fees);

        assertEq(clones.length, 2);
        assertEq(relayer.balance, fees[0] + fees[1]);
        assertEq(founderWallet.balance, (1 ether - fees[0]) + (2 ether - fees[1]));
    }

    function test_BatchDeployAndSweepRevertsOnLengthMismatch() public {
        bytes32[] memory salts = new bytes32[](2);
        address[][] memory tokensPerSalt = new address[][](1);
        uint256[] memory fees = new uint256[](2);

        vm.expectRevert("ForwarderFactory: length mismatch");
        factory.batchDeployAndSweep(salts, tokensPerSalt, fees);
    }

    // -- Reentrancy ------------------------------------------------------

    function test_ReentrantFeeRecipientCannotExtractExtraFunds() public {
        bytes32 salt = keccak256("invoice-reentrancy");
        address depositAddress = factory.computeAddress(salt);
        _payNative(depositAddress, 1 ether);

        ReentrantFeeRecipient attacker = new ReentrantFeeRecipient();

        vm.prank(relayer);
        address clone = factory.deployAndSweep(salt, _emptyTokens(), 0);
        attacker.setTarget(Forwarder(payable(clone)), MAX_SWEEP_FEE);

        _payNative(depositAddress, 1 ether);

        vm.expectRevert();
        Forwarder(payable(clone)).sweep(_emptyTokens(), MAX_SWEEP_FEE, address(attacker));

        // The whole call reverted: no funds moved anywhere.
        assertEq(depositAddress.balance, 1 ether);
        assertEq(address(attacker).balance, 0);
    }
}
