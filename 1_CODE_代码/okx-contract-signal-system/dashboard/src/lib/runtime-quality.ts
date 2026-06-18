type RuntimeQualityInputs = {
  runtimeOperational: boolean;
  runtimeReason?: string | null;
  closedBackfillOperational: boolean;
  latestScanPushAllowed: boolean;
  manifestReason: string;
};

export function runtimePushAllowed({
  runtimeOperational,
  closedBackfillOperational,
  latestScanPushAllowed,
}: RuntimeQualityInputs): boolean {
  return runtimeOperational && closedBackfillOperational && latestScanPushAllowed;
}

export function runtimeBlockingReasons(inputs: RuntimeQualityInputs): string[] {
  if (runtimePushAllowed(inputs)) {
    return [];
  }
  return [
    ...(inputs.runtimeOperational ? [] : [inputs.runtimeReason ?? "runtime_not_online"]),
    ...(inputs.closedBackfillOperational ? [] : ["closed_backfill_incomplete"]),
    ...(inputs.runtimeOperational ? [inputs.manifestReason] : []),
  ];
}
