import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { SubjectTermStats } from "../api/types";
import { termTrends } from "../lib/charts";
import { formatNumber } from "../lib/format";
import { CHART_COLORS } from "../lib/palette";
import { ChartData } from "./ChartData";


export function TermLineChart({ groups }: { groups: SubjectTermStats[] }) {
  const { terms, subjects } = termTrends(groups);
  if (terms.length === 0) return <p>No term statistics.</p>;
  return (
    <figure>
      <figcaption>Average score per term, by subject</figcaption>
      <div role="img" aria-label={`Line chart of the average score over ${terms.length} terms for ${subjects.length} subjects`}>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={terms} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="term" />
            <YAxis domain={[0, 100]} />
            <Tooltip formatter={(value) => formatNumber(Number(value))} />
            <Legend />
            {subjects.map((subject, index) => (
              <Line
                key={subject}
                type="monotone"
                dataKey={subject}
                stroke={CHART_COLORS[index % CHART_COLORS.length]}
                connectNulls={false}
                dot
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <ChartData caption="the term averages">
        <thead>
          <tr>
            <th>Term</th>
            {subjects.map((subject) => (
              <th key={subject} className="num">
                {subject}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {terms.map((row) => (
            <tr key={row.term}>
              <td>{row.term}</td>
              {subjects.map((subject) => (
                <td key={subject} className="num">
                  {formatNumber(row[subject] as number | null)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </ChartData>
    </figure>
  );
}
