# Proxy-contract sweep strategy — design doc (Phase 2, draft)

## Goal

Every deposit address is backed by a small on-chain contract/account whose *only* job is
to forward whatever it receives to one hardcoded founder wallet address. The forwarding
destination is baked in at deploy time and cannot be changed by any admin call, upgrade,
or configuration — so "strict to founder wallet" is a property enforced by the contract
itself, not by operational discipline. This document is the design for that pattern
across the four target chain families. **No contract code ships from this doc — it's the
architecture to review before any Solidity/FunC/Anchor is written.**

This is Phase 2 work: it plugs into the Phase 1 abstractions (`AddressProvider`,
`SweepStrategy`, `ChainClient`) documented in [README.md](../README.md) — this doc doesn't
change those contracts, it describes what a *real* implementation of them looks like for
the `COUNTERFACTUAL_CREATE2` strategy plus a new PDA-based strategy for Solana.

Build order (per prior decision): **EVM first**, fully validated end-to-end, then reuse it
as the template for Tron, TON, and Solana in that rough order (Tron is closest to EVM;
TON and Solana are architecturally furthest from it).

## Shared design principles across all four chains

1. **Immutable destination.** The founder wallet address is embedded in the contract's
   bytecode/init data (EVM/Tron), state init (TON), or program-derived seed (Solana) at
   the moment the deposit address is computed — never stored in mutable state, never
   settable through an admin function.
2. **Permissionless sweep.** Anyone (not just the operator) can trigger the
   forward-to-founder call. This matters because it means funds can never get stuck
   waiting on an operator key that's lost, compromised, or just slow — the contract's own
   logic is the only thing that decides where funds go, and it only has one possible
   answer.
3. **Deposit address computable before deployment.** All four chains support some form of
   "the address exists and can receive funds before the contract/account is actually
   deployed." `AddressProvider.allocate_address()` only ever does this off-chain
   computation — it never submits a transaction. Deployment happens lazily, at sweep
   time, driven by `SweepStrategy.sweep()`.
4. **No upgradability.** These are single-purpose forwarders holding funds transiently
   between deposit and sweep, not long-lived vaults — there's nothing to upgrade, and an
   upgrade path would just be an attack surface. If a bug is found, stop routing new
   invoices to the flawed factory/program version and ship a fixed one; funds already
   swept are unaffected.
5. **No per-invoice secrets.** Nothing about the deposit address relies on a private key
   the host application must safeguard (contrast with naive `HD_DERIVED`, where a leaked
   xprv/seed compromises every address). Compromise of the factory/program deployer key
   does not expose funds already swept, and does not let an attacker redirect the
   founder-wallet destination of already-computed addresses.

## Per-chain design

### EVM-like chains (Ethereum, and any EVM L2/sidechain — build target #1)

**Pattern:** CREATE2 factory deploying EIP-1167 minimal proxy clones. This is the
standard "counterfactual deposit address" pattern (Argent, early Tornado Cash relayers,
many exchange deposit-address systems use variants of it).

- A `ForwarderFactory` contract is deployed once per chain. It exposes
  `computeAddress(salt) -> address` (pure, no state change), `deployAndSweep(salt, tokens,
  feeAmount)` (deploys the clone via `CREATE2` if not already deployed, then calls its
  `sweep()`), and `batchDeployAndSweep(salts, tokensPerSalt, feeAmounts)` (the same for
  many invoices in one transaction, amortizing the base tx cost across all of them).
- The clone's implementation contract is a minimal forwarder: on `sweep()`, it pays the
  caller `feeAmount` in native currency (capped by an immutable `MAX_SWEEP_FEE`, and by
  whatever native balance the clone actually holds) to cover the blockchain fee they paid
  to call it, then transfers the rest of its native-token balance, and the balance of any
  ERC-20 addresses passed in, to an **immutable** `FOUNDER_WALLET` address baked into the
  factory's init code (so every clone, once its address is derived from
  `keccak256(factory ++ salt ++ initCodeHash)`, has that same destination compiled in —
  changing the founder wallet means deploying a new factory version, which naturally
  produces new deposit addresses, never silently redirects existing ones).
- `salt` is derived from the invoice id (e.g. `keccak256(invoiceId)`), giving a stable,
  deterministic 1:1 mapping between invoice and address without needing to store the
  mapping on-chain.
- ERC-20 sweeping needs a token allowlist known at sweep time (an ERC-20 forwarder can't
  discover "which tokens might have been sent" on its own) — `SweepStrategy.sweep()` is
  responsible for passing the relevant token addresses in, based on what
  `ChainClient.list_incoming_transactions()` observed.
- **The blockchain fee for calling `deployAndSweep` is paid out of the swept funds, not by
  the operator.** The relayer supplies the actual `feeAmount` it wants reimbursed per
  call (tracking real gas cost at call time), but that amount is hard-capped by the
  immutable `MAX_SWEEP_FEE` set when the factory is deployed — so a compromised or
  misbehaving relayer can claim at most that fixed ceiling per sweep, never redirect the
  bulk of the funds. A clone with no native balance (an ERC-20-only deposit) simply can't
  be charged a fee — the relayer requests `feeAmount = 0` for those. The relayer key can
  still grief availability (delay sweeps, or decline to sweep fee-less token-only
  deposits) but — because the forwarder logic is immutable and permissionless — can never
  redirect funds, even if compromised.

**Open question for implementation phase:** whether to self-destruct the clone after
sweep to reclaim gas refund. Post-Dencun (EIP-6780), `SELFDESTRUCT` only refunds/deletes
when called in the same transaction as contract creation, which fits `deployAndSweep`
doing both in one tx — worth confirming against target chain(s) if any are pre-Dencun
forks.

### Tron

**Pattern:** identical to EVM — TVM has supported `CREATE2` since the 2022 GreatVoyage
upgrade, and Tron is close enough to EVM-compatible (Solidity, similar opcodes) that the
same factory/clone contracts should deploy with little to no modification. Differences to
account for at implementation time, not design time:

- Resource model is energy/bandwidth rather than gas — the relayer account needs enough
  of both staked/available, and cost estimation differs from `eth_estimateGas`.
- TRC-20 (Tron's ERC-20 analogue) is the token standard to sweep, mechanically identical
  in shape to the EVM ERC-20 case above.
- Address format/encoding differs (base58check `T...`) but the underlying CREATE2
  derivation is the same 20-byte address space.

### TON

**Pattern:** TON has a *more* native version of this than CREATE2 — a contract's address
is deterministically `hash(StateInit)`, where `StateInit` is (code, initial data). That
hash is computable entirely off-chain, before any deployment, and TON accounts can
receive funds while "uninitialized" (address exists, no code deployed yet) exactly like
the EVM counterfactual case.

- The forwarder contract's initial data includes the immutable founder wallet address
  (and, if useful, the invoice id/salt) — so, same as the EVM case, the destination is
  fixed at address-computation time, not at deployment time.
- Deployment happens by sending an internal message to the computed address *with* the
  `StateInit` attached — the TON runtime deploys the contract as part of processing that
  message. In practice this collapses "deploy" and "first sweep call" into a single
  message the same way `deployAndSweep` does for EVM.
- TON's account model (and jetton — TON's fungible-token standard — transfers in
  particular) has more moving parts than EVM's; jetton sweeping specifically needs a
  jetton-wallet-address lookup per token per forwarder, which should get its own design
  pass at implementation time rather than being hand-waved here.

### Solana

**Pattern:** per decision above — a single Anchor program deployed once, with a **PDA
(Program Derived Address) per invoice** as the deposit address, rather than "a contract
per invoice." This is Solana's structural equivalent of the CREATE2 pattern: a PDA is
deterministically derived off-chain from `(program_id, seeds)` — seeds being the invoice
id — via `Pubkey.findProgramAddress`, exists as a valid receiving address with no
deployment step at all (Solana has no separate "deploy an account" action the way EVM
does), and is owned by the program, so only that program's instructions can move funds
out of it.

- The program's `sweep` instruction takes the invoice's PDA and transfers its lamport
  (and, for SPL tokens, associated-token-account) balance to the founder wallet. As with
  the EVM case, the founder wallet should be a compile-time constant in the program, not
  a mutable account field — an admin-configurable destination would reopen exactly the
  redirection risk this whole design exists to close.
- Because a PDA has no private key (it's not on the ed25519 curve), only the owning
  program can authorize a transfer out of it — this is a stronger guarantee than "the
  logic happens to always forward," closer to "it is structurally impossible for anything
  but this program's sweep instruction to move these funds."
- SPL token sweeping requires the invoice's associated token account(s) to exist before
  the token transfer can land — unlike native SOL, an ATA needs its own (permissionless,
  fee-payer-agnostic) creation step, which `SweepStrategy` needs to account for as a
  possible pre-step rather than assuming the destination is always ready.

## Mapping onto the Phase 1 contracts

| Phase 1 contract | Real implementation responsibility under this design |
|---|---|
| `AddressProvider.allocate_address()` | Off-chain address/PDA derivation only (factory address + salt for EVM/Tron, `hash(StateInit)` for TON, `findProgramAddress` for Solana). Never submits a transaction. |
| `ChainClient` | Balance/tx-history reads against the counterfactual address — works identically whether or not the forwarder is deployed yet, since the address is valid and fundable either way. |
| `SweepStrategy.sweep()` | Submits the real deploy-and-forward transaction (EVM/Tron `deployAndSweep`, TON message-with-`StateInit`, Solana `sweep` instruction), passing along which token/jetton/SPL-mint addresses were observed so the forwarder knows what to move. |
| `PaymentService` | Unchanged — it already calls `sweep()` with only the incrementally-new amount since the last check (see [CLAUDE.md](../CLAUDE.md)), which composes correctly with "sweep call may also pay for deployment the first time." |

## Explicitly not covered by this doc

Actual contract/program source code, factory deployment addresses, relayer key
management/custody, gas/energy/fee funding strategy for the relayer, monitoring/alerting
for stuck sweeps, and jetton/SPL-token-specific edge cases. These are implementation-phase
concerns for each chain once its design is reviewed and approved.

## Proposed next step

Design + implement the EVM `ForwarderFactory` + minimal-proxy clone as a standalone,
independently testable unit (likely its own `contracts/` subtree with Foundry or
Hardhat, separate from the Python package), then wire a real `AddressProvider`/
`SweepStrategy` pair against it before starting Tron.
