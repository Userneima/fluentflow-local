import {Navigate, Route, Routes} from 'react-router-dom';

// A route registry is a plain array of entries, one per route:
//
//   {path, element: () => node}  render a view
//   {path, redirectTo}           declarative redirect, kept as data so the
//                                route contract stays testable
export const RegistryRoutes = ({registry}) => (
    <Routes>
        {registry.map((entry) => (
            <Route
                key={entry.path}
                path={entry.path}
                element={entry.redirectTo ? <Navigate to={entry.redirectTo} replace/> : entry.element()}
            />
        ))}
    </Routes>
);
