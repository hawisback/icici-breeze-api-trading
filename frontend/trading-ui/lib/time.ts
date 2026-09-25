export const IST_TIME_ZONE = "Asia/Kolkata";

type TimeValue = string | number | Date | null | undefined;

function asDate(value: TimeValue, unixSeconds = false): Date | null {
  if (value == null || value === "") return null;
  const date =
    value instanceof Date
      ? value
      : typeof value === "number"
        ? new Date(unixSeconds ? value * 1000 : value)
        : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatISTTime(
  value: TimeValue,
  options: Intl.DateTimeFormatOptions = {},
): string {
  const date = asDate(value);
  if (!date) return "--";
  return (
    date.toLocaleTimeString("en-IN", {
      timeZone: IST_TIME_ZONE,
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      ...options,
    }) + " IST"
  );
}

export function formatISTDateTime(
  value: TimeValue,
  options: Intl.DateTimeFormatOptions = {},
  unixSeconds = false,
): string {
  const date = asDate(value, unixSeconds);
  if (!date) return "--";
  return (
    date.toLocaleString("en-IN", {
      timeZone: IST_TIME_ZONE,
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      ...options,
    }) + " IST"
  );
}

export function getISTMarketSessionInfo(value: Date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: IST_TIME_ZONE,
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(value);
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const hour = Number(map.hour);
  const minute = Number(map.minute);
  const timeInMinutes = hour * 60 + minute;
  const isWeekday = ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(map.weekday);
  const isMarketHours =
    timeInMinutes >= 9 * 60 + 15 && timeInMinutes <= 15 * 60 + 30;
  const isOpen = isWeekday && isMarketHours;

  return {
    isOpen,
    statusText: isOpen
      ? "MARKET OPEN (09:15-15:30 IST)"
      : "MARKET CLOSED",
  };
}
