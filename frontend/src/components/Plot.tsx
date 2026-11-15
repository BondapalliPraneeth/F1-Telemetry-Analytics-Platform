import createPlotlyComponent from "react-plotly.js/factory";
import Plotly from "plotly.js-basic-dist-min";
import type { CSSProperties } from "react";

const PlotlyComponent = createPlotlyComponent(Plotly);

export const COLORS = { a: "#38bdf8", b: "#f59e0b", delta: "#a78bfa", grid: "#27272a", text: "#d4d4d8" };

export function baseLayout(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: COLORS.text, size: 12 },
    margin: { l: 56, r: 16, t: 24, b: 40 },
    legend: { orientation: "h", y: 1.15 },
    ...extra,
  };
}

export const axis = (title: string, extra: Record<string, unknown> = {}) => ({
  title: { text: title },
  gridcolor: COLORS.grid,
  zerolinecolor: "#52525b",
  ...extra,
});

interface Props {
  data: unknown[];
  layout?: Record<string, unknown>;
  style?: CSSProperties;
}

export default function Plot({ data, layout = {}, style }: Props) {
  return (
    <PlotlyComponent
      data={data}
      layout={baseLayout(layout)}
      config={{ displaylogo: false, responsive: true }}
      useResizeHandler
      style={{ width: "100%", height: "100%", ...style }}
    />
  );
}
