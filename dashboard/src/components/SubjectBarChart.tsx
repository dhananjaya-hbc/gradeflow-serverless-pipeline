import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { SubjectTermStats } from "../api/types";
import { subjectAverages } from "../lib/charts";
import { formatCount, formatNumber, formatPercent } from "../lib/format";
import { BAR_COLOR } from "../lib/palette";
import { ChartData } from "./ChartData";

export function SubjectBarChart({ groups }: { groups: SubjectTermStats[] }) {
  const data = subjectAverages(groups);
  if (data.length === 0) return <p>No subject statistics.</p>;
  return (
    <figure>
      <figcaption>Average score by subject (all terms)</figcaption>
      <div role="img" aria-label={`Bar chart of the average score for ${data.length} subjects`}>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="subject" />
            <YAxis domain={[0, 100]} />
            <Tooltip formatter={(value) => formatNumber(Number(value))} />
            <Bar dataKey="average" name="Average" fill={BAR_COLOR} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ChartData caption="the subject averages">
        <thead>
          <tr>
            <th>Subject</th>
            <th className="num">Average</th>
            <th className="num">Pass rate</th>
            <th className="num">Scores</th>
          </tr>
        </thead>
        <tbody>
          {data.map((row) => (
            <tr key={row.subject}>
              <td>{row.subject}</td>
              <td className="num">{formatNumber(row.average)}</td>
              <td className="num">{formatPercent(row.passRate)}</td>
              <td className="num">{formatCount(row.count)}</td>
            </tr>
          ))}
        </tbody>
      </ChartData>
    </figure>
  );
}
