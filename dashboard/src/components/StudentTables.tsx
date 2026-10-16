import type { StatsResponse, StudentAverage } from "../api/types";
import { formatNumber } from "../lib/format";
import { TableScroll } from "./TableScroll";

function StudentTable({ title, students }: { title: string; students: StudentAverage[] }) {
  return (
    <div>
      <h3>{title}</h3>
      {students.length === 0 ? (
        <p>None.</p>
      ) : (
        <TableScroll label={title}>
        <table>
          <thead>
            <tr>
              <th>Student</th>
              <th className="num">Average</th>
            </tr>
          </thead>
          <tbody>
            {students.map((s) => (
              <tr key={s.student_id}>
                <td>
                  {s.student_name} <span className="muted">{s.student_id}</span>
                </td>
                <td className="num">{formatNumber(s.average)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </TableScroll>
      )}
    </div>
  );
}

export function StudentTables({ stats }: { stats: StatsResponse }) {
  const classes = Object.entries(stats.class_averages).sort(([a], [b]) => a.localeCompare(b));
  return (
    <div className="student-tables">
      <StudentTable title="Top students" students={stats.top_students} />
      <StudentTable title="Lowest students" students={stats.bottom_students} />
      <div>
        <h3>Class averages</h3>
        {classes.length === 0 ? (
          <p>None.</p>
        ) : (
          <TableScroll label="Class averages">
          <table>
            <thead>
              <tr>
                <th>Class</th>
                <th className="num">Average</th>
              </tr>
            </thead>
            <tbody>
              {classes.map(([name, average]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td className="num">{formatNumber(average)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </TableScroll>
        )}
      </div>
    </div>
  );
}
