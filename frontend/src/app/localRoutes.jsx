import {lazy} from 'react';

const MediaText = lazy(() => import('../routes/media-text.jsx'));
const AgentTasks = lazy(() => import('../routes/agent-tasks.jsx'));
const AgentTrace = lazy(() => import('../routes/agent-trace.jsx'));
const Editor = lazy(() => import('../routes/editor.jsx'));
const Settings = lazy(() => import('../routes/settings.jsx'));
const About = lazy(() => import('../routes/about.jsx'));
const WorkspaceApi = lazy(() => import('../routes/workspace-api.jsx'));

// Local-edition route registry. The root opens the processing workspace
// directly ('/' redirects to /media-text); there is no landing page, access
// gate, admin console, or guest fallback. Tested against the manifest's
// frontend_contract in localRoutes.test.jsx.
export const localRouteRegistry = [
    {path: '/', redirectTo: '/media-text'},
    {path: '/media-text', element: () => <MediaText/>},
    {path: '/agent', element: () => <AgentTasks/>},
    {path: '/tasks', redirectTo: '/agent'},
    {path: '/tasks/:taskId/agent', element: () => <AgentTrace/>},
    {path: '/editor', element: () => <Editor/>},
    {path: '/settings', element: () => <Settings/>},
    {path: '/workspace/api', element: () => <WorkspaceApi/>},
    {path: '/about', element: () => <About/>},
    {path: '/about/:page', element: () => <About/>},
    {path: '*', redirectTo: '/media-text'},
];
