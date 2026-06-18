type RuntimeQualityInputs = {
  runtimeOperational: boolean;
  runtimeReason?: string | null;
  closedBackfillOperational: boolean;
  latestScanPushAllowed: boolean;
  manifestReason: string;
};

export function resolveManifestReason({
  manifestStatusReason,
  latestScanPresent,
  latestScanPushAllowed,
}: {
  manifestStatusReason: unknown;
  latestScanPresent: boolean;
  latestScanPushAllowed: boolean;
}): string {
  if (typeof manifestStatusReason === "string" && manifestStatusReason.trim()) {
    return manifestStatusReason.trim();
  }
  if (latestScanPushAllowed) {
    return "approved_manifest_valid";
  }
  return latestScanPresent ? "runtime_manifest_not_approved" : "runtime_status_missing";
}

export function runtimePushAllowed({
  runtimeOperational,
  latestScanPushAllowed,
}: RuntimeQualityInputs): boolean {
  return runtimeOperational && latestScanPushAllowed;
}

export function runtimeOperationalReady(inputs: RuntimeQualityInputs): boolean {
  return runtimePushAllowed(inputs) && inputs.closedBackfillOperational;
}

export function runtimeBlockingReasons(inputs: RuntimeQualityInputs): string[] {
  if (runtimePushAllowed(inputs)) {
    return [];
  }
  const reasons: string[] = [];
  if (!inputs.runtimeOperational) {
    reasons.push(inputs.runtimeReason?.trim() || "runtime_not_online");
  }
  if (!inputs.latestScanPushAllowed) {
    const manifestReason = inputs.manifestReason.trim();
    reasons.push(
      manifestReason && manifestReason !== "approved_manifest_valid"
        ? manifestReason
        : "runtime_manifest_not_approved",
    );
  }
  return [...new Set(reasons)];
}

export function runtimeHealthBlockingReasons(inputs: RuntimeQualityInputs): string[] {
  return inputs.closedBackfillOperational ? [] : ["closed_backfill_incomplete"];
}
