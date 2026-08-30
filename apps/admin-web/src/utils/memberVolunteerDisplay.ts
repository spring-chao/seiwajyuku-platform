export type VolunteerScopeLevel = "REGIONAL_CENTER" | "CLASS" | "GROUP" | "ANY";

export type VolunteerPositionDisplayOption = {
  position_key: string;
  position_name: string;
  scope_level: VolunteerScopeLevel;
  is_active: boolean;
  sort_order: number;
  capabilities: string[];
  capability_names: string[];
};

const scopeLevels: VolunteerScopeLevel[] = [
  "REGIONAL_CENTER",
  "CLASS",
  "GROUP",
  "ANY"
];

function safePositionName(
  positionKey: string,
  positionName: string | null | undefined,
  fallback: string
) {
  const value = positionName?.trim() || "";
  return value &&
    value !== positionKey &&
    !/^volunteer_[a-z0-9_]+$/i.test(value)
    ? value
    : fallback;
}

export function isVolunteerScopeLevel(
  value?: string | null
): value is VolunteerScopeLevel {
  return Boolean(value && scopeLevels.includes(value as VolunteerScopeLevel));
}

/**
 * Keep the select readable while the catalog request is still pending or
 * temporarily unavailable. The fallback is deliberately display-only and
 * never exposes the internal position key.
 */
export function buildCurrentVolunteerPositionOptions(
  catalog: readonly VolunteerPositionDisplayOption[],
  currentKey?: string | null,
  currentName?: string | null,
  currentScopeLevel?: string | null
) {
  const options = catalog.map(item => ({
    ...item,
    position_name: safePositionName(
      item.position_key,
      item.position_key === currentKey
        ? currentName
        : item.position_name,
      ""
    ) ||
      safePositionName(item.position_key, item.position_name, "") ||
      "志工服务（待核对）"
  }));
  if (!currentKey || options.some(item => item.position_key === currentKey)) {
    return options;
  }
  options.push({
    position_key: currentKey,
    position_name: safePositionName(
      currentKey,
      currentName,
      "志工服务（待核对）"
    ),
    scope_level: isVolunteerScopeLevel(currentScopeLevel)
      ? currentScopeLevel
      : "ANY",
    is_active: true,
    sort_order: Number.MAX_SAFE_INTEGER,
    capabilities: [],
    capability_names: []
  });
  return options;
}

export function volunteerPositionLabel(
  positionKey: string | null | undefined,
  positionName: string | null | undefined,
  catalog: readonly VolunteerPositionDisplayOption[]
) {
  const catalogName = catalog.find(item => item.position_key === positionKey);
  return (
    safePositionName(positionKey || "", positionName, "") ||
    safePositionName(positionKey || "", catalogName?.position_name, "") ||
    "志工服务（待核对）"
  );
}

export function volunteerAppointmentStatusLabel(status?: string | null) {
  return (
    (
      {
        PLANNED: "待开始",
        ACTIVE: "服务中",
        SUSPENDED: "已暂停",
        ENDED: "已结束",
        REVOKED: "已撤销"
      } as Record<string, string>
    )[status || ""] ?? "待核对"
  );
}

export function shouldShowLegacyVolunteerHint(
  historicalName?: string | null,
  currentPositionName?: string | null
) {
  const historical = historicalName?.trim() || "";
  return Boolean(
    historical && historical !== (currentPositionName?.trim() || "")
  );
}
