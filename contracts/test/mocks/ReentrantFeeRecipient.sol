// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Forwarder} from "../../src/Forwarder.sol";

/// @notice Test-only attacker: on receiving its fee payment, immediately
/// tries to call `sweep` again on the same clone, attempting to extract a
/// second fee payment before the original call's founder-forward completes.
contract ReentrantFeeRecipient {
    Forwarder public target;
    address[] internal reentryTokens;
    uint256 public reentryFeeAmount;
    bool internal attemptedReentry;

    function setTarget(Forwarder target_, uint256 reentryFeeAmount_) external {
        target = target_;
        reentryFeeAmount = reentryFeeAmount_;
    }

    receive() external payable {
        if (!attemptedReentry) {
            attemptedReentry = true;
            target.sweep(reentryTokens, reentryFeeAmount, address(this));
        }
    }
}
