"""
The grab policy engine.

`evaluate_policy()` runs AFTER the filter gate says "allow" but BEFORE
the actual .torrent fetch. It decides whether the economic cost of
grabbing a torrent is acceptable given the user's current ratio,
wedge balance, and the torrent's VIP/freeleech status.

The policy is a pure function over plain dataclasses — no I/O, no side
effects — so it's trivial to unit-test. The async orchestration layer
(fetching user status and torrent info from MAM) is the caller's
responsibility; this module only evaluates the data it's handed.

Decision matrix:

    1. If torrent is VIP and policy_vip_always_grab → GRAB, tier=vip
    2. If torrent is free (VIP, global FL, personal FL) → GRAB, tier=free
    3. If policy_vip_only → SKIP (torrent isn't VIP, we checked above)
    4. If policy_use_wedge AND wedges > min_reserved → GRAB, tier=wedge
    5. If policy_free_only → SKIP (not free and no wedge available/allowed)
    6. If policy_ratio_floor > 0 AND ratio < floor → SKIP, tier=ratio_too_low
    7. Otherwise → GRAB, tier=normal (user's ratio can absorb it)

Every decision includes a `tier` string for audit logging — the caller
writes it to the `grabs` table so we can reconstruct why each grab
was made or skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


Action = Literal["grab", "skip"]


@dataclass(frozen=True)
class PolicyConfig:
    """User-configured grab-policy rules.

    Built from settings.json by the caller.
    """

    vip_only: bool = False
    free_only: bool = False
    vip_always_grab: bool = True
    use_wedge: bool = False
    min_wedges_reserved: int = 0
    ratio_floor: float = 0.0


@dataclass(frozen=True)
class EconomicContext:
    """The economic inputs for a single policy evaluation.

    `announce_vip` comes from the IRC announce parser (the `(VIP)` flag).
    The remaining fields come from the torrent-info API lookup and the
    user-status API. Any field left at its default means the data wasn't
    available (e.g. the torrent-info lookup was disabled or failed).
    """

    # From IRC announce
    announce_vip: bool = False

    # From torrent-info API lookup (may be None if lookup disabled/failed)
    torrent_vip: Optional[bool] = None
    torrent_free: Optional[bool] = None
    torrent_fl_vip: Optional[bool] = None
    personal_freeleech: Optional[bool] = None

    # From user-status API
    user_ratio: Optional[float] = None
    user_wedges: Optional[int] = None

    @property
    def is_vip(self) -> bool:
        """True if ANY source says this torrent is VIP."""
        if self.announce_vip:
            return True
        if self.torrent_vip is True:
            return True
        return False

    @property
    def is_free(self) -> bool:
        """True if the torrent is free by any mechanism (VIP, FL, personal FL)."""
        if self.is_vip:
            return True
        if self.torrent_free is True:
            return True
        if self.torrent_fl_vip is True:
            return True
        if self.personal_freeleech is True:
            return True
        return False


@dataclass(frozen=True)
class PolicyDecision:
    """Output of `evaluate_policy`.

    `tier` is the policy tier that matched, used for audit logging:
      - "vip"           — grabbed because VIP and vip_always_grab
      - "free"          — grabbed because already free (VIP/FL/personal FL)
      - "wedge"         — grabbed after deciding to spend a freeleech wedge
      - "normal"        — grabbed, not free, ratio absorbs it
      - "vip_required"  — skipped, policy_vip_only and torrent isn't VIP
      - "free_required" — skipped, policy_free_only and no free path available
      - "ratio_too_low" — skipped, ratio below floor
      - "wedge_reserve" — skipped, would use wedge but below reserve threshold

    `use_wedge` is True when the decision is to grab AND spend a wedge.
    The caller must append `&fl` to the download URL.
    """

    action: Action
    tier: str
    use_wedge: bool = False


# ─── The engine ─────────────────────────────────────────────


def evaluate_policy(
    ctx: EconomicContext,
    config: PolicyConfig,
) -> PolicyDecision:
    """Decide whether the economic cost of grabbing this torrent is acceptable.

    Pure function. The caller handles I/O (API lookups) and persistence
    (writing the tier to the grabs table).
    """
    # Step 1: VIP fast-path. If the torrent is VIP and the user wants
    # VIP torrents grabbed unconditionally, short-circuit everything.
    if ctx.is_vip and config.vip_always_grab:
        return PolicyDecision(action="grab", tier="vip")

    # Step 2: Free check. If the torrent is already free by any
    # mechanism, there's no economic cost — grab it.
    if ctx.is_free:
        return PolicyDecision(action="grab", tier="free")

    # Step 3: VIP-only gate. If the user only wants VIP torrents and
    # this one isn't, skip it.
    if config.vip_only:
        return PolicyDecision(action="skip", tier="vip_required")

    # Step 4: Wedge path. If the user allows wedge spending and has
    # enough wedges above the reserve, we can make it free.
    if config.use_wedge:
        wedges = ctx.user_wedges
        if wedges is not None and wedges > config.min_wedges_reserved:
            return PolicyDecision(action="grab", tier="wedge", use_wedge=True)
        # Wedge enabled but not enough wedges — fall through to free_only
        # and ratio checks. If free_only is set, this will be a skip.
        if config.free_only:
            return PolicyDecision(action="skip", tier="wedge_reserve")

    # Step 5: Free-only gate. If the user only wants free torrents and
    # we haven't found a way to make it free, skip.
    if config.free_only:
        return PolicyDecision(action="skip", tier="free_required")

    # Step 6: Ratio floor. If the user set a minimum ratio and they're
    # below it, skip to protect their account health.
    if config.ratio_floor > 0:
        ratio = ctx.user_ratio
        if ratio is not None and ratio < config.ratio_floor:
            return PolicyDecision(action="skip", tier="ratio_too_low")

    # Step 7: Normal grab. The torrent isn't free, but the user hasn't
    # set any gates that would prevent grabbing it.
    return PolicyDecision(action="grab", tier="normal")
