export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.hostname === "www.onechapter.am") {
      url.hostname = "onechapter.am";
      url.protocol = "https:";
      console.log(
        JSON.stringify({
          message: "canonical_redirect",
          from: request.url,
          to: url.toString(),
        }),
      );
      return Response.redirect(url.toString(), 301);
    }

    return env.ASSETS.fetch(request);
  },
};
