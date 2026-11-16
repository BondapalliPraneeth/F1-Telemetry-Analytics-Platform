export interface Session {
  id: number;
  year: number;
  round: number;
  event: string;
  session_type: string;
  location: string;
  n_laps: number;
}
export interface Driver {
  code: string;
  number: string;
  team: string | null;
}
export interface Lap {
  driver: string;
  lap_number: number;
  lap_time_s: number | null;
  stint: number | null;
  compound: string | null;
  has_telemetry: boolean;
  is_accurate: boolean;
  pit_in: boolean;
  pit_out: boolean;
}
export interface Stint {
  driver: string;
  stint_number: number;
  compound: string | null;
  lap_start: number;
  lap_end: number;
  n_laps: number;
}
export interface Trace {
  driver: string;
  lap_number: number;
  lap_time_s: number | null;
  time_s: (number | null)[];
  speed: (number | null)[];
  throttle: (number | null)[];
  brake: (number | null)[];
  gear: (number | null)[];
}
export interface Corner {
  number: number;
  letter: string;
  distance_m: number;
}
export interface Sector {
  name: string;
  a_s: number | null;
  b_s: number | null;
  delta_s: number | null;
}
export interface MiniSector {
  index: number;
  start_m: number;
  end_m: number;
  a_s: number;
  b_s: number;
  delta_s: number;
}
export interface Compare {
  session_id: number;
  step_m: number;
  distance: number[];
  delta: (number | null)[];
  a: Trace;
  b: Trace;
  corners: Corner[];
  sectors: Sector[];
  mini_sectors: MiniSector[];
  final_delta_s: number | null;
}
export interface Degradation {
  driver: string;
  stint: number;
  compound: string | null;
  fuel_effect_s_per_lap: number;
  note: string;
  points: { lap_number: number; tyre_age: number; lap_time_s: number; corrected_s: number }[];
  fit: {
    slope: number;
    intercept: number;
    stderr: number;
    ci_low: number;
    ci_high: number;
    r2: number;
    n: number;
    confidence: number;
  };
  band: { tyre_age: number; fit: number; low: number; high: number }[];
  cleaning: {
    total: number;
    pit_laps: number;
    safety_car_laps: number;
    first_lap: number;
    missing_time: number;
    kept: number;
  };
}
export interface ReplayCar {
  driver: string;
  x: number;
  y: number;
  speed: number | null;
  lap: number;
  distance_m: number | null;
}
export type ReplayMsg =
  | {
      type: "hello";
      t0: number;
      t1: number;
      speed: number;
      drivers: string[];
      teams: Record<string, string>;
      track: { x: number[]; y: number[] };
    }
  | { type: "frame"; t: number; speed: number; playing: boolean; cars: ReplayCar[] }
  | { type: "end"; t: number }
  | { type: "error"; detail: string };
