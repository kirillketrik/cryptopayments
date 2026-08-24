// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Clones} from "@openzeppelin/contracts/proxy/Clones.sol";
import {Forwarder} from "./Forwarder.sol";

/// @notice Deploys one {Forwarder} implementation at construction time and,
/// from then on, produces deterministic (CREATE2) deposit addresses for it —
/// one address per `salt` (derived off-chain from an invoice id) — that can
/// receive funds before the clone is ever deployed. See
/// docs/proxy-contract-strategy.md at the repo root for the full design.
///
/// `founderWallet` is immutable and is baked into every clone's shared
/// implementation bytecode via the {Forwarder} constructor below, not into
/// clone storage: every deposit address produced by this factory forwards to
/// the same fixed destination, and that can only change by deploying a new
/// factory (which naturally produces a new, distinct set of addresses) —
/// never by reconfiguring existing ones.
contract ForwarderFactory {
    address public immutable implementation;
    address public immutable founderWallet;
    uint256 public immutable maxSweepFee;

    event Deployed(address indexed clone, bytes32 indexed salt);

    constructor(address founderWallet_, uint256 maxSweepFee_) {
        founderWallet = founderWallet_;
        maxSweepFee = maxSweepFee_;
        implementation = address(new Forwarder(founderWallet_, maxSweepFee_));
    }

    /// @notice The deposit address for `salt`, computable off-chain before
    /// the clone is deployed. `AddressProvider.allocate_address()` calls the
    /// real-implementation equivalent of this — a pure computation, no
    /// transaction — to hand out an invoice's deposit address.
    function computeAddress(bytes32 salt) public view returns (address) {
        return Clones.predictDeterministicAddress(implementation, salt, address(this));
    }

    /// @notice Deploy the clone for `salt` if it isn't already deployed,
    /// then sweep its current native + `tokens` balances to
    /// {founderWallet}, paying the caller `feeAmount` (native currency,
    /// capped by {Forwarder-MAX_SWEEP_FEE}) out of the swept funds to cover
    /// the blockchain fee they paid for this call. Permissionless and
    /// idempotent: safe to call again on an already-deployed clone (it just
    /// sweeps again, with feeAmount = 0 for a no-op re-check), and safe to
    /// call before the clone has ever received anything.
    function deployAndSweep(bytes32 salt, address[] calldata tokens, uint256 feeAmount)
        external
        returns (address clone)
    {
        return _deployAndSweep(salt, tokens, feeAmount, msg.sender);
    }

    /// @notice Batched form of {deployAndSweep}: sweeps many invoices in one
    /// transaction, amortizing the base transaction cost across all of them.
    /// `salts`, `tokensPerSalt`, and `feeAmounts` must be the same length,
    /// index-aligned per invoice. All fee payments go to the caller.
    function batchDeployAndSweep(
        bytes32[] calldata salts,
        address[][] calldata tokensPerSalt,
        uint256[] calldata feeAmounts
    ) external returns (address[] memory clones) {
        require(
            salts.length == tokensPerSalt.length && salts.length == feeAmounts.length,
            "ForwarderFactory: length mismatch"
        );

        clones = new address[](salts.length);
        for (uint256 i = 0; i < salts.length; i++) {
            clones[i] = _deployAndSweep(salts[i], tokensPerSalt[i], feeAmounts[i], msg.sender);
        }
    }

    function _deployAndSweep(bytes32 salt, address[] calldata tokens, uint256 feeAmount, address feeRecipient)
        internal
        returns (address clone)
    {
        address predicted = computeAddress(salt);
        if (predicted.code.length == 0) {
            clone = Clones.cloneDeterministic(implementation, salt);
            emit Deployed(clone, salt);
        } else {
            clone = predicted;
        }

        Forwarder(payable(clone)).sweep(tokens, feeAmount, feeRecipient);
    }
}
