import {Navigate, Route, Routes} from 'react-router-dom';

// A route registry is a plain array of entries, one per route:
//
//   {path, element: (ctx) => node}  render a view; ctx carries shell state
//                                   (hosted passes {guestMode}, local passes {})
//   {path, redirectTo}              declarative redirect, kept as data so
//                                   edition route contracts stay testable
//
// The hosted and local composition roots each own one registry and render it
// through this shared component, mirroring the backend's shared router
// factories with edition adapters.
export const RegistryRoutes = ({registry, ctx = {}}) => (
    <Routes>
        {registry.map((entry) => (
            <Route
                key={entry.path}
                path={entry.path}
                element={entry.redirectTo ? <Navigate to={entry.redirectTo} replace/> : entry.element(ctx)}
            />
        ))}
    </Routes>
);
