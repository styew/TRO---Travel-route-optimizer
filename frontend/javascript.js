const searchButton = document.getElementById("search_button");

searchButton.addEventListener("click", async () => {

    const origin = document.getElementById("origin").value;
    const destination = document.getElementById("destination").value;
    const date = document.getElementById("date").value;

    console.log("Sending search to backend...");

    const response = await fetch(
        "http://localhost:8000/search",
        {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                origin: origin,
                destination: destination,
                date: date
            })
        }
    );

    const data = await response.json();

    console.log("Backend response:", data);
});