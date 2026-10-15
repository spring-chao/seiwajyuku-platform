const calendar = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit"
});

export function chinaDate(now: Date = new Date()): string {
  const parts = calendar.formatToParts(now);
  const value = (type: string) => parts.find(part => part.type === type)?.value;
  return `${value("year")}-${value("month")}-${value("day")}`;
}

function utcInstant(value: string | Date): Date {
  // API audit timestamps without an offset are UTC DATETIME values.
  const raw =
    typeof value === "string" && !/(Z|[+-]\d{2}:?\d{2})$/i.test(value)
      ? `${value.replace(" ", "T")}Z`
      : value;
  return new Date(raw);
}

export function chinaTimestampDate(value: string | Date): string {
  return chinaDate(utcInstant(value));
}

export function chinaTime(value: string | Date): string {
  const parsed = utcInstant(value);
  if (!Number.isFinite(parsed.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23"
  }).format(parsed);
}

/** One check can be shared by interval, resume and visibility events. */
export function createBusinessDayRefresh(
  readDate: () => string,
  refresh: () => void
) {
  let date = readDate();
  return () => {
    const current = readDate();
    if (current === date) return;
    date = current;
    refresh();
  };
}
