#!/bin/bash
# set-irq-affinity.sh — keep interrupt handling off the isolated audio cores.
#
# Cores 1,2,3 are isolated for JACK and the plugins (isolcpus, see CLAUDE.md).
# Interrupts delivered there are exactly the jitter that isolation exists to
# prevent, so every IRQ that can be steered is pinned to core 0, which is also
# where the UI and the rest of the OS live.
#
# ## Why this runs more than once
#
# It used to be a one-liner in cpu-performance.service, run once. It silently
# did nothing for most devices: measured on a CM5, 44 of the board's IRQs were
# still set to `0-3` after boot, including mmc0/mmc1 (SD and SDIO), the audio
# DMA channel and the GPU. Running the same loop by hand afterwards fixed 21 of
# them immediately — so the commands were right and the timing was wrong. An
# IRQ only exists once its driver has probed and registered it, and anything
# registering after the single pass keeps the kernel's default all-CPU mask.
#
# It was working by luck rather than by configuration: the GIC was delivering
# everything to core 0 anyway, which is why no xruns pointed at it. Luck is not
# an invariant — a driver reload or a kernel change could start delivering on
# the DSP cores with nothing to stop it.
#
# So: apply, wait, apply again. Nothing is ordered after this unit, so taking
# half a minute over it costs nothing.
#
# ## What "refused" means
#
# About two dozen IRQs reject the write with EPERM, and that is correct rather
# than a failure: per-CPU interrupts (arch_timer, ptimer, vtimer, arm-pmu) are
# bound to their own core by the kernel and have no affinity to set. They are
# counted separately so a real regression doesn't hide among them.

set -u

CORE0_MASK=1
PASSES="${IRQ_AFFINITY_PASSES:-3}"
SETTLE="${IRQ_AFFINITY_SETTLE:-10}"

apply_pass() {
	local pinned=0 refused=0
	for dir in /proc/irq/[0-9]*/; do
		if echo "${CORE0_MASK}" > "${dir}smp_affinity" 2>/dev/null; then
			pinned=$((pinned + 1))
		else
			refused=$((refused + 1))
		fi
	done

	# Best effort: the I2S/DAC interrupt alongside JACK on core 1. The pattern
	# is heuristic and on the CM5 matches nothing — the per-period audio work
	# arrives on a DMA channel (dw_axi_dmac_platform) instead. Leaving it on
	# core 0 is harmless, it is still off the DSP cores; moving it is a change
	# to make with xrun measurements in hand, not on a guess. See CLAUDE.md,
	# "do not change without measuring".
	local n
	for n in $(grep -iE "i2s|pcm51|hifiberry" /proc/interrupts \
		| sed -E "s/^[[:space:]]*([0-9]+):.*/\1/"); do
		echo 2 > "/proc/irq/${n}/smp_affinity" 2>/dev/null || true
	done

	echo "irq affinity: ${pinned} pinned to core 0, ${refused} kernel-managed"
}

for pass in $(seq 1 "${PASSES}"); do
	apply_pass
	[ "${pass}" -lt "${PASSES}" ] && sleep "${SETTLE}"
done

# What actually ended up off core 0, so the journal answers "did this work"
# without anyone having to go and look.
stragglers=0
for dir in /proc/irq/[0-9]*/; do
	[ "$(cat "${dir}smp_affinity_list" 2>/dev/null)" = "0" ] || stragglers=$((stragglers + 1))
done
echo "irq affinity: ${stragglers} IRQs remain off core 0 (expected: per-CPU only)"
