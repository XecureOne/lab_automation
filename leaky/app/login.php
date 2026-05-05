<?php
include "db.php";

$msg = "";

if (isset($_POST['username']) && isset($_POST['password'])) {
    $u = $_POST['username'];
    $p = $_POST['password'];

    $query = "SELECT * FROM users WHERE username='$u' AND password='$p'";
    $result = $conn->query($query);

    if ($result && $result->num_rows > 0) {
        $msg = "Login successful!";
    } else {
        $msg = "Invalid credentials.";
    }
}
?>

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Login - BSoft</title>
</head>
<body>
    <h2>Login</h2>

    <form method="POST">
        Username: <input type="text" name="username"><br><br>
        Password: <input type="password" name="password"><br><br>
        <button type="submit">Login</button>
    </form>

    <p><?php echo $msg; ?></p>
</body>
</html>
